"""
Vespera Cleanup Crew
--------------------
Runs continuously in parallel with the background loop.
Pulls memories from the 'recent' layer, evaluates them,
and either promotes them to 'validated' or prunes them.
"""

import json
import time
import requests
from datetime import datetime, timezone
from config import OLLAMA_URL, OLLAMA_MODEL, CLEANUP_INTERVAL as RUN_INTERVAL_SECONDS, CLEANUP_BATCH_SIZE as BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory, get_stats

CLEANUP_PROMPT = """You are the Cleanup Crew for a persistent AI memory system.

Review the following memory and decide what to do with it.

Memory:
{content}

Evaluate it against these rules:
- DELETE if it is: highly repetitive, completely incoherent, pure rambling with no value, or contradicts important facts
- KEEP if it is: a coherent thought, useful technical insight, or meaningful reference to a past conversation

Respond in JSON only. No explanation.
{{
  "decision": "keep" or "delete",
  "reason": "one short sentence explaining why"
}}"""


def call_local_model(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[CleanupCrew] Model error: {e}")
        return None


def parse_decision(raw: str) -> dict | None:
    try:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(raw[start:end])
    except Exception:
        return None


def review_memory(memory: dict) -> tuple[str, str]:
    raw = call_local_model(CLEANUP_PROMPT.format(content=memory["content"]))
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
    print(f"[CleanupCrew] Started — every {RUN_INTERVAL_SECONDS}s")
    while True:
        try:
            run_cleanup()
        except Exception as e:
            print(f"[CleanupCrew] Error: {e}")
        time.sleep(RUN_INTERVAL_SECONDS)


if __name__ == "__main__":
    import sys
    run_once() if "--once" in sys.argv else run_loop()
