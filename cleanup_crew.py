"""
Vespera Cleanup Crew
--------------------
First-pass memory reviewer. Runs every 5 minutes.
Reviews 'recent' memories — promotes good ones to 'validated', prunes garbage.
"""

import time
import threading
from config import get_component, CLEANUP_INTERVAL, CLEANUP_BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory
from utils import get_logger, parse_json_response, _sanitize

log = get_logger("cleanup_crew")

_cfg         = get_component("cleanup_crew")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
BATCH_SIZE   = CLEANUP_BATCH_SIZE

CLEANUP_PROMPT = """You are reviewing a memory for a persistent AI system.

Memory:
{content}

DELETE if: highly repetitive, incoherent, pure rambling, or contradicts known facts.
KEEP if: coherent thought, useful technical insight, or meaningful reference.

Respond in JSON only:
{{
  "decision": "keep" or "delete",
  "reason": "one short sentence"
}}"""


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.1, timeout=30)


def review_memory(memory: dict) -> tuple[str, str]:
    raw = call_local(CLEANUP_PROMPT.format(content=_sanitize(memory["content"], 500)))
    if not raw:
        return "keep", "model unavailable"
    result = parse_json_response(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response"
    decision = result.get("decision")
    if not decision or not isinstance(decision, str):
        return "keep", "missing or null decision"
    return decision.lower(), _sanitize(result.get("reason", ""), 500)  # sanitize model output before storage


def run_cleanup():
    # Process oldest-reviewed first — consistent with periodic_pruning's anti-starvation fix.
    # Under a burst of background-loop output, newest entries would otherwise eclipse older ones.
    memories = get_memories(layer="recent", limit=BATCH_SIZE, order_by="created_at ASC")
    if not memories:
        log.debug("Nothing to review.")
        return
    log.info("Reviewing %d memories...", len(memories))
    kept = pruned = 0
    for memory in memories:
        decision, reason = review_memory(memory)
        if decision not in {"keep", "delete"}:
            log.warning("Unexpected decision '%s' for memory %s — treating as keep", decision, memory["id"][:8])
            decision = "keep"
        short_id = memory["id"][:8]
        if decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="cleanup_crew")
            log.info("PRUNED  %s — %s", short_id, reason)
            pruned += 1
        else:
            # Use 0.5 as a floor only — never downgrade a memory that already has a
            # higher trust score (e.g. one manually set to 0.8 by periodic_pruning).
            existing_score = memory.get("trust_score") or 0.0
            new_score = max(0.5, existing_score)
            promote_memory(memory["id"], new_trust_score=new_score)
            log.info("KEPT    %s → validated (trust=%.2f) | %s...", short_id, new_score, memory["content"][:60])
            kept += 1
    log.info("Done — kept: %d, pruned: %d", kept, pruned)


_shutdown = threading.Event()  # fallback used when run_loop() is called without an event

def run_loop(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    log.info("Started — model: %s — every %ss", OLLAMA_MODEL, CLEANUP_INTERVAL)
    while not evt.is_set():
        try:
            run_cleanup()
        except Exception as e:
            log.error("Error: %s", e)
        evt.wait(CLEANUP_INTERVAL)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        run_cleanup()
    else:
        run_loop()
