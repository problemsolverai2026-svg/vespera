"""
Vespera Cleanup Crew
--------------------
Runs continuously in parallel with the background loop.
Pulls memories from the 'recent' layer, evaluates them,
and either promotes them to 'validated' or prunes them.

Uses the cleanup crew prompt from the Vespera architecture spec.
Model: configurable — defaults to local Ollama, falls back to cloud.
"""

import os
import json
import time
import requests
from datetime import datetime, timezone
from memory.store import (
    init_db,
    get_memories,
    promote_memory,
    prune_memory,
    get_stats,
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"       # swap to 7B/13B when ready
RUN_INTERVAL_SECONDS = 300          # check every 5 minutes
BATCH_SIZE = 5                      # memories to review per run

CLEANUP_PROMPT = """You are the Cleanup Crew for a persistent AI memory system.

Review the following memory and decide what to do with it.

Memory:
{content}

Evaluate it against these rules:
- DELETE if it is: highly repetitive, completely incoherent, pure rambling with no value, or contradicts important facts
- KEEP if it is: a coherent thought, useful technical insight, or meaningful reference to a past conversation

Respond in JSON only. No explanation. Format:
{{
  "decision": "keep" or "delete",
  "reason": "one short sentence explaining why"
}}"""


# ─────────────────────────────────────────────
# MODEL CALL
# ─────────────────────────────────────────────

def call_local_model(prompt: str) -> str:
    """Call Ollama local model. Returns raw text response."""
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
        print(f"[CleanupCrew] Local model error: {e}")
        return None


def parse_decision(raw: str) -> dict:
    """Extract JSON decision from model response."""
    try:
        # Find the JSON block
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(raw[start:end])
    except Exception:
        return None


# ─────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────

def review_memory(memory: dict) -> tuple[str, str]:
    """
    Review a single memory.
    Returns (decision, reason) where decision is 'keep' or 'delete'.
    Falls back to 'keep' if model is unavailable.
    """
    prompt = CLEANUP_PROMPT.format(content=memory["content"])
    raw = call_local_model(prompt)

    if not raw:
        print(f"[CleanupCrew] No model response — defaulting to keep for {memory['id'][:8]}")
        return "keep", "model unavailable, defaulting to keep"

    result = parse_decision(raw)
    if not result or "decision" not in result:
        print(f"[CleanupCrew] Could not parse response — defaulting to keep for {memory['id'][:8]}")
        return "keep", "unparseable response, defaulting to keep"

    return result["decision"].lower(), result.get("reason", "no reason given")


def run_cleanup():
    """One cleanup pass — review a batch of recent memories."""
    memories = get_memories(layer="recent", limit=BATCH_SIZE)

    if not memories:
        print(f"[CleanupCrew] Nothing to review.")
        return

    print(f"[CleanupCrew] Reviewing {len(memories)} memories...")
    kept = 0
    pruned = 0

    for memory in memories:
        decision, reason = review_memory(memory)
        short_id = memory["id"][:8]
        preview = memory["content"][:60]

        if decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="cleanup_crew")
            print(f"[CleanupCrew] ✗ PRUNED  {short_id} — {reason}")
            pruned += 1
        else:
            promote_memory(memory["id"], new_trust_score=0.5)
            print(f"[CleanupCrew] ✓ KEPT    {short_id} → validated | {preview}...")
            kept += 1

    print(f"[CleanupCrew] Pass complete — kept: {kept}, pruned: {pruned}")


# ─────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────

def run_loop():
    """Run cleanup crew continuously."""
    init_db()
    print(f"[CleanupCrew] Started. Checking every {RUN_INTERVAL_SECONDS}s.")

    while True:
        try:
            print(f"\n[CleanupCrew] {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} — running pass...")
            run_cleanup()
            stats = get_stats()
            print(f"[CleanupCrew] Stats — recent: {stats['recent']}, validated: {stats['validated']}, core: {stats['core']}")
        except Exception as e:
            print(f"[CleanupCrew] Error during pass: {e}")

        time.sleep(RUN_INTERVAL_SECONDS)


# ─────────────────────────────────────────────
# SINGLE PASS (for testing without loop)
# ─────────────────────────────────────────────

def run_once():
    """Run one cleanup pass and exit. Good for testing."""
    init_db()
    print("[CleanupCrew] Running single pass...")
    run_cleanup()
    print("\n[CleanupCrew] Final stats:")
    for k, v in get_stats().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
