"""
Vespera Cleanup Crew
--------------------
First-pass memory reviewer. Runs every 5 minutes.
Reviews 'recent' memories — promotes good ones to 'validated', prunes garbage.
"""

import json
import time
import requests
from config import get_component, CLEANUP_INTERVAL, CLEANUP_BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory, get_stats

_cfg = get_component("cleanup_crew")
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
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[CleanupCrew] Model error: {e}")
        return None


def parse_decision(raw: str) -> dict | None:
    try:
        start = raw.find("{"); end = raw.rfind("}") + 1
        return json.loads(raw[start:end]) if start != -1 and end > 0 else None
    except Exception:
        return None


def review_memory(memory: dict) -> tuple[str, str]:
    raw = call_local(CLEANUP_PROMPT.format(content=memory["content"]))
    if not raw:
        return "keep", "model unavailable"
    result = parse_decision(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response"
    return result["decision"].lower(), result.get("reason", "")


def run_cleanup():
    memories = get_memories(layer="recent", limit=BATCH_SIZE)
    if not memories:
        print("[CleanupCrew] Nothing to review.")
        return
    print(f"[CleanupCrew] Reviewing {len(memories)} memories...")
    kept = pruned = 0
    for memory in memories:
        decision, reason = review_memory(memory)
        short_id = memory["id"][:8]
        preview  = memory["content"][:60]
        if decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="cleanup_crew")
            print(f"[CleanupCrew] ✗ PRUNED  {short_id} — {reason}")
            pruned += 1
        else:
            promote_memory(memory["id"], new_trust_score=0.5)
            print(f"[CleanupCrew] ✓ KEPT    {short_id} → validated | {preview}...")
            kept += 1
    print(f"[CleanupCrew] Done — kept: {kept}, pruned: {pruned}")


def run_once():
    init_db()
    run_cleanup()
    for k, v in get_stats().items():
        print(f"  {k}: {v}")


def run_loop():
    init_db()
    print(f"[CleanupCrew] Started — model: {OLLAMA_MODEL} — every {CLEANUP_INTERVAL}s")
    while True:
        try:
            run_cleanup()
        except Exception as e:
            print(f"[CleanupCrew] Error: {e}")
        time.sleep(CLEANUP_INTERVAL)


if __name__ == "__main__":
    import sys
    run_once() if "--once" in sys.argv else run_loop()
