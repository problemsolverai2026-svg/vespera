"""
Vespera Periodic Pruning
------------------------
Deep memory reviewer. Runs every 3 days.
Stricter than cleanup crew — promotes the best to 'core', prunes the rest.
"""

import time
import threading
from pathlib import Path
from config import get_component, PRUNING_INTERVAL_DAYS, PRUNING_BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory, touch_memory
from utils import get_logger, parse_json_response, _sanitize

log = get_logger("periodic_pruning")

_cfg         = get_component("periodic_pruning")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
BATCH_SIZE   = PRUNING_BATCH_SIZE

PRUNING_PROMPT = """You are performing a deep review of a persistent AI memory about a user.

Memory:
{content}

Core memories (permanent) for reference:
{core_memories}

Decide:
- promote = genuinely valuable, worth keeping permanently in core
- keep    = fine to hold, not ready for core yet
- delete  = outdated, redundant, contradicts core, or not worth keeping

IMPORTANT: Personal facts about the user are HIGH VALUE. This includes:
- Names, relationships, and who they know
- Preferences and favorites (food, music, hobbies, sports, shows)
- Habits and routines
- Goals, projects, and decisions
- Job, location, and life context
Do NOT delete a memory just because it is personal rather than technical. Personal facts are exactly what this system is designed to remember.

Respond in JSON only:
{{
  "decision": "promote" or "keep" or "delete",
  "reason": "one short sentence"
}}"""

CONTRADICTION_PROMPT = """You are a memory consistency checker for a persistent AI memory system.

New memory being considered for core:
{new_memory}

Existing core memories:
{core_memories}

Does the new memory directly contradict any existing core memory?
A contradiction means the two memories assert opposing facts (e.g. different names, opposite preferences, conflicting facts about the user).
Do NOT flag a memory as contradicting just because it adds new information or is on the same topic.

If there IS a contradiction, identify which core memory conflicts and what the conflict is.

Respond in JSON only:
{{
  "contradicts": true or false,
  "conflicting_core": "quote the conflicting core memory here, or null if none",
  "reason": "one short sentence explaining the conflict, or null if none"
}}"""


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.1, num_predict=150)


def get_core_context() -> str:
    core = get_memories(layer="core", limit=10)
    return "\n".join([f"- {_sanitize(m['content'], 150)}" for m in core]) if core else "No core memories yet."


def check_contradiction(content: str, core_context: str) -> tuple[bool, str | None, str | None]:
    """Check if content contradicts any existing core memory.
    Returns (contradicts, conflicting_core_text, reason)."""
    raw = call_local(CONTRADICTION_PROMPT.format(
        new_memory=_sanitize(content, 500),
        core_memories=core_context,
    ))
    if not raw:
        return False, None, None  # model unavailable — don't block promotion
    result = parse_json_response(raw)
    if not result:
        return False, None, None
    contradicts = result.get("contradicts", False)
    if not isinstance(contradicts, bool):
        contradicts = str(contradicts).lower() == "true"
    conflicting = _sanitize(result.get("conflicting_core") or "", 200) or None
    reason = _sanitize(result.get("reason") or "", 200) or None
    return contradicts, conflicting, reason


def review_memory(memory: dict, core_context: str) -> tuple[str, str]:
    raw = call_local(PRUNING_PROMPT.format(content=_sanitize(memory["content"], 500), core_memories=core_context))
    if not raw:
        return "keep", "model unavailable"
    result = parse_json_response(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response"
    decision = result.get("decision")
    if not decision or not isinstance(decision, str):
        return "keep", "missing or null decision"
    decision = decision.strip().lower()
    return (decision if decision in ("promote", "keep", "delete") else "keep"), _sanitize(result.get("reason", ""), 500)  # sanitize model output before storage


def run_pruning():
    if not _run_lock.acquire(blocking=False):
        log.info("run_pruning() skipped — already running.")
        return
    try:
        _run_pruning_inner()
    finally:
        _run_lock.release()


def _run_pruning_inner():
    # Sort oldest-reviewed first so newer 'keep' decisions don't permanently
    # eclipse older memories that never get a chance to be evaluated.
    validated = get_memories(layer="validated", limit=BATCH_SIZE, order_by="updated_at ASC")
    if not validated:
        log.debug("Nothing to prune.")
        return
    core_context = get_core_context()
    promoted = kept = pruned = 0
    for memory in validated:
        decision, reason = review_memory(memory, core_context)
        # Follow-up questions are transient re-engagement prompts, not durable facts.
        # Block promotion to core AND block deletion — only mark_followup_used() should remove them.
        if memory.get("source") == "followup" and decision in ("promote", "delete"):
            decision = "keep"
        if decision == "promote":
            # Contradiction check before committing to core
            contradicts, conflicting, contra_reason = check_contradiction(memory["content"], core_context)
            if contradicts:
                # Hold in validated — don't promote, don't delete, let human or future pass decide
                touch_memory(memory["id"])
                log.warning(
                    "CONFLICT %s — contradicts core: %s | reason: %s",
                    memory["id"][:8],
                    (conflicting or "")[:80],
                    contra_reason or "unknown",
                )
                kept += 1
            else:
                promote_memory(memory["id"], new_trust_score=0.95)
                log.info("PROMOTED %s → core", memory["id"][:8])
                promoted += 1
        elif decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="periodic_pruning")
            log.info("PRUNED   %s — %s", memory["id"][:8], reason)
            pruned += 1
        else:
            # Touch the memory so it sorts to the back of the queue next run,
            # giving older un-reviewed memories a chance to be evaluated.
            touch_memory(memory["id"])
            kept += 1
    log.info("Done — promoted: %d, kept: %d, deleted: %d", promoted, kept, pruned)


_shutdown  = threading.Event()
_run_lock  = threading.Lock()   # prevents manual API trigger overlapping with background loop
# Store last-run timestamp in ~/.vespera/ (user-writable) rather than the project
# directory so read-only deployments (Docker, /opt/) don't silently fail to persist it.
_LAST_RUN_KEY = Path.home() / ".vespera" / ".pruning_last_run"


def _should_run() -> bool:
    """Return True only if enough time has passed since last pruning run."""
    interval = PRUNING_INTERVAL_DAYS * 24 * 60 * 60
    try:
        last = float(_LAST_RUN_KEY.read_text().strip())
        return (time.time() - last) >= interval
    except Exception:
        return True  # no record = run it


def _mark_ran():
    """Atomically write the last-run timestamp — temp file + rename prevents partial reads."""
    try:
        tmp = _LAST_RUN_KEY.with_suffix(".tmp")
        tmp.write_text(str(time.time()))
        tmp.replace(_LAST_RUN_KEY)
    except Exception:
        pass


def run_loop(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    # Poll every hour rather than every interval — that way a restart only delays
    # a due pruning run by at most 1 hour instead of a full 3-day interval.
    # _should_run() checks the timestamp so actual pruning still only happens every
    # PRUNING_INTERVAL_DAYS days.
    poll_interval = 3600
    log.info("Started — model: %s — every %d days (checked hourly)", OLLAMA_MODEL, PRUNING_INTERVAL_DAYS)
    while not evt.is_set():
        try:
            if _should_run():
                run_pruning()
                _mark_ran()
            else:
                log.debug("Skipping pruning — not enough time since last run.")
        except Exception as e:
            log.error("Error: %s", e)
        evt.wait(poll_interval)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        run_pruning()
    else:
        run_loop()
