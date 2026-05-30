"""
Vespera Cleanup Crew
--------------------
First-pass memory reviewer. Runs every 5 minutes.
Reviews 'recent' memories — promotes good ones to 'validated', prunes garbage.
"""

import time
import threading
import requests
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
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.1},
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message", {}).get("content") or data.get("response", "")).strip()
    except Exception as e:
        log.error("Model error: %s", e)
        return None


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
    memories = get_memories(layer="recent", limit=BATCH_SIZE)
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
            promote_memory(memory["id"], new_trust_score=0.5)
            log.info("KEPT    %s → validated | %s...", short_id, memory["content"][:60])
            kept += 1
    log.info("Done — kept: %d, pruned: %d", kept, pruned)


_shutdown = threading.Event()

def run_loop(shutdown_event: threading.Event = None):
    global _shutdown
    if shutdown_event:
        _shutdown = shutdown_event
    init_db()
    log.info("Started — model: %s — every %ss", OLLAMA_MODEL, CLEANUP_INTERVAL)
    while not _shutdown.is_set():
        try:
            run_cleanup()
        except Exception as e:
            log.error("Error: %s", e)
        _shutdown.wait(CLEANUP_INTERVAL)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        run_cleanup()
    else:
        run_loop()
