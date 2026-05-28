"""
Vespera Periodic Pruning
------------------------
Deep clean that runs every 3-4 days.
Reviews the 'validated' layer with stricter criteria than the cleanup crew.
Promotes the best memories to 'core', removes anything no longer relevant.

Single model — stricter prompt. No 3-model consensus needed for local personal memory.
(3-model consensus only applies at the Agora publish gate, not here.)
"""

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

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"        # swap to 7B/13B when ready
RUN_EVERY_DAYS = 3                  # how often to run (days)
BATCH_SIZE     = 20                 # memories to review per run


PRUNING_PROMPT = """You are performing a deep memory review for a persistent AI system.

This memory has already passed an initial cleanup. Now apply stricter criteria.

Memory:
{content}

Core memories (the most trusted, permanent layer) for reference:
{core_memories}

Answer these three questions:

1. Is this still relevant to recent conversations, or is it outdated?
2. Does this contradict or conflict with anything in the core memories?
3. Is this strong enough to be promoted to permanent core memory, or should it stay as validated?

Respond in JSON only. No explanation outside the JSON.
{{
  "decision": "promote" or "keep" or "delete",
  "reason": "one short sentence"
}}

Rules:
- promote = genuinely valuable, worth keeping permanently
- keep    = fine to hold onto, not ready for core yet
- delete  = outdated, redundant, contradicts core, or just not worth keeping"""


# ─────────────────────────────────────────────
# MODEL
# ─────────────────────────────────────────────

def call_local_model(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 150}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[PeriodicPruning] Model error: {e}")
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


# ─────────────────────────────────────────────
# CORE LOGIC
# ─────────────────────────────────────────────

def get_core_context() -> str:
    """Pull core memories as reference for contradiction checking."""
    core = get_memories(layer="core", limit=10)
    if not core:
        return "No core memories yet."
    return "\n".join([f"- {m['content'][:150]}" for m in core])


def review_memory(memory: dict, core_context: str) -> tuple[str, str]:
    """
    Deep review of a single validated memory.
    Returns (decision, reason) — promote / keep / delete.
    """
    prompt = PRUNING_PROMPT.format(
        content=memory["content"],
        core_memories=core_context,
    )

    raw = call_local_model(prompt)
    if not raw:
        return "keep", "model unavailable — defaulting to keep"

    result = parse_decision(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response — defaulting to keep"

    decision = result["decision"].lower()
    if decision not in ("promote", "keep", "delete"):
        decision = "keep"

    return decision, result.get("reason", "no reason given")


def run_pruning():
    """One full periodic pruning pass."""
    validated = get_memories(layer="validated", limit=BATCH_SIZE)

    if not validated:
        print("[PeriodicPruning] Nothing to review in validated layer.")
        return

    print(f"[PeriodicPruning] Deep reviewing {len(validated)} memories...")
    core_context = get_core_context()

    promoted = 0
    kept     = 0
    pruned   = 0

    for memory in validated:
        decision, reason = review_memory(memory, core_context)
        short_id = memory["id"][:8]
        preview  = memory["content"][:60]

        if decision == "promote":
            promote_memory(memory["id"], new_trust_score=0.95)
            print(f"[PeriodicPruning] ⬆ PROMOTED {short_id} → core | {preview}...")
            promoted += 1
        elif decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="periodic_pruning")
            print(f"[PeriodicPruning] ✗ DELETED  {short_id} — {reason}")
            pruned += 1
        else:
            print(f"[PeriodicPruning] ○ KEPT     {short_id} | {preview}...")
            kept += 1

    print(f"\n[PeriodicPruning] Pass complete — promoted: {promoted}, kept: {kept}, deleted: {pruned}")


# ─────────────────────────────────────────────
# SCHEDULER
# ─────────────────────────────────────────────

def run_loop():
    """Run periodic pruning on a schedule."""
    init_db()
    interval = RUN_EVERY_DAYS * 24 * 60 * 60
    print(f"[PeriodicPruning] Scheduled every {RUN_EVERY_DAYS} days.")

    while True:
        try:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            print(f"\n[PeriodicPruning] {ts} — starting deep clean...")
            run_pruning()
            print(f"\n[PeriodicPruning] Stats:")
            for k, v in get_stats().items():
                print(f"  {k}: {v}")
        except Exception as e:
            print(f"[PeriodicPruning] Error: {e}")

        print(f"[PeriodicPruning] Next run in {RUN_EVERY_DAYS} days.")
        time.sleep(interval)


def run_once():
    """Single pass — for testing."""
    init_db()
    print("[PeriodicPruning] Running single deep clean pass...")
    run_pruning()
    print("\n[PeriodicPruning] Final stats:")
    for k, v in get_stats().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
