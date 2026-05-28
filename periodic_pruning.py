"""
Vespera Periodic Pruning
------------------------
Deep memory reviewer. Runs every 3 days.
Stricter than cleanup crew — promotes best memories to permanent 'core',
removes anything outdated or redundant.
"""

import json
import time
import requests
from config import get_component, PRUNING_INTERVAL_DAYS, PRUNING_BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory, get_stats

_cfg = get_component("periodic_pruning")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
BATCH_SIZE   = PRUNING_BATCH_SIZE

PRUNING_PROMPT = """You are performing a deep review of a persistent AI memory.

This memory already passed an initial cleanup. Apply stricter criteria.

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
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.1, "num_predict": 150}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[PeriodicPruning] Model error: {e}")
        return None


def parse_decision(raw: str) -> dict | None:
    try:
        start = raw.find("{"); end = raw.rfind("}") + 1
        return json.loads(raw[start:end]) if start != -1 and end > 0 else None
    except Exception:
        return None


def get_core_context() -> str:
    core = get_memories(layer="core", limit=10)
    return "\n".join([f"- {m['content'][:150]}" for m in core]) if core else "No core memories yet."


def review_memory(memory: dict, core_context: str) -> tuple[str, str]:
    raw = call_local(PRUNING_PROMPT.format(content=memory["content"], core_memories=core_context))
    if not raw:
        return "keep", "model unavailable"
    result = parse_decision(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response"
    decision = result["decision"].lower()
    return (decision if decision in ("promote","keep","delete") else "keep"), result.get("reason","")


def run_pruning():
    validated = get_memories(layer="validated", limit=BATCH_SIZE)
    if not validated:
        print("[PeriodicPruning] Nothing to review.")
        return
    print(f"[PeriodicPruning] Deep reviewing {len(validated)} memories...")
    core_context = get_core_context()
    promoted = kept = pruned = 0
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
    print(f"[PeriodicPruning] Done — promoted: {promoted}, kept: {kept}, deleted: {pruned}")


def run_once():
    init_db()
    run_pruning()
    for k, v in get_stats().items():
        print(f"  {k}: {v}")


def run_loop():
    init_db()
    interval = PRUNING_INTERVAL_DAYS * 24 * 60 * 60
    print(f"[PeriodicPruning] Started — model: {OLLAMA_MODEL} — every {PRUNING_INTERVAL_DAYS} days")
    while True:
        try:
            run_pruning()
        except Exception as e:
            print(f"[PeriodicPruning] Error: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    import sys
    run_once() if "--once" in sys.argv else run_loop()
