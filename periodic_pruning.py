"""
Vespera Periodic Pruning
------------------------
Deep memory reviewer. Runs every 3 days.
Stricter than cleanup crew — promotes the best to 'core', prunes the rest.
"""

import time
import threading
import requests
from config import get_component, PRUNING_INTERVAL_DAYS, PRUNING_BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory
from utils import get_logger, parse_json_response, _sanitize

log = get_logger("periodic_pruning")

_cfg         = get_component("periodic_pruning")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
BATCH_SIZE   = PRUNING_BATCH_SIZE

PRUNING_PROMPT = """You are performing a deep review of a persistent AI memory.

Memory:
{content}

Core memories (permanent) for reference:
{core_memories}

Decide:
- promote = genuinely valuable, worth keeping permanently in core
- keep    = fine to hold, not ready for core yet
- delete  = outdated, redundant, contradicts core, or not worth keeping

Respond in JSON only:
{{
  "decision": "promote" or "keep" or "delete",
  "reason": "one short sentence"
}}"""


def call_local(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 150},
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message", {}).get("content") or data.get("response", "")).strip()
    except Exception as e:
        log.error("Model error: %s", e)
        return None


def get_core_context() -> str:
    core = get_memories(layer="core", limit=10)
    return "\n".join([f"- {_sanitize(m['content'], 150)}" for m in core]) if core else "No core memories yet."


def review_memory(memory: dict, core_context: str) -> tuple[str, str]:
    raw = call_local(PRUNING_PROMPT.format(content=_sanitize(memory["content"], 500), core_memories=core_context))
    if not raw:
        return "keep", "model unavailable"
    result = parse_json_response(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response"
    decision = result["decision"].strip().lower()
    return (decision if decision in ("promote", "keep", "delete") else "keep"), result.get("reason", "")


def run_pruning():
    validated = get_memories(layer="validated", limit=BATCH_SIZE)
    if not validated:
        log.debug("Nothing to prune.")
        return
    core_context = get_core_context()
    promoted = kept = pruned = 0
    for memory in validated:
        decision, reason = review_memory(memory, core_context)
        if decision == "promote":
            promote_memory(memory["id"], new_trust_score=0.95)
            log.info("PROMOTED %s → core", memory["id"][:8])
            promoted += 1
        elif decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="periodic_pruning")
            log.info("PRUNED   %s — %s", memory["id"][:8], reason)
            pruned += 1
        else:
            kept += 1
    log.info("Done — promoted: %d, kept: %d, deleted: %d", promoted, kept, pruned)


_shutdown = threading.Event()

def run_loop(shutdown_event: threading.Event = None):
    global _shutdown
    if shutdown_event:
        _shutdown = shutdown_event
    init_db()
    interval = PRUNING_INTERVAL_DAYS * 24 * 60 * 60
    log.info("Started — model: %s — every %d days", OLLAMA_MODEL, PRUNING_INTERVAL_DAYS)
    while not _shutdown.is_set():
        try:
            run_pruning()
        except Exception as e:
            log.error("Error: %s", e)
        _shutdown.wait(interval)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        run_pruning()
    else:
        run_loop()
