"""
Vespera Background Loop
-----------------------
Persistent thinking engine. Runs 24/7, lightly reviews past conversations,
generates brief thoughts, saves to 'recent' memory layer.
Uses web search for technical gaps instead of calling the cloud model.
"""

import json
import time
import random
import requests
from config import get_component, BACKGROUND_LOOP_INTERVAL, MAX_THOUGHT_LENGTH
from web_search import search as _web_search
from memory.store import init_db, add_memory, get_memories, get_recent_conversations, get_stats

_cfg = get_component("background_loop")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
RUN_INTERVAL_SECONDS = BACKGROUND_LOOP_INTERVAL

BACKGROUND_PROMPT = """You are a persistent AI memory system. Lightly review past conversations and generate one brief, focused thought.

Focus ONLY on technical concepts. Ignore emotions.

Past conversation:
{conversation}

Recent memories:
{memories}

Task:
1. Identify the core technical idea
2. If you don't understand something technical, say: SEARCH: <question>
3. Otherwise write ONE short thought (2-3 sentences max)

Rules:
- Do NOT repeat what was already said — add a new angle
- If nothing new to add, say: NOTHING_NEW
- Max {max_length} characters"""

WEB_SEARCH_SUMMARY_PROMPT = """Web search result:
Question: {question}
Result: {result}

Summarize in 1-2 sentences, technically focused."""


def call_local(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.3, "num_predict": 200}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[BackgroundLoop] Model error: {e}")
        return None


def web_search(query: str) -> str | None:
    try:
        result = _web_search(query)
        return result[:500] if result else None
    except Exception as e:
        print(f"[BackgroundLoop] Search error: {e}")
        return None


def pick_conversation() -> str:
    convs = get_recent_conversations(limit=20)
    if not convs:
        return "No conversations yet."
    selected = convs[:4] if random.random() < 0.7 or len(convs) <= 3 else convs[random.randint(0, max(0, len(convs)-4)):random.randint(0, max(0, len(convs)-4))+4]
    return "\n".join([f"{c['role'].upper()}: {c['content'][:200]}" for c in reversed(selected)])


def get_memory_context() -> str:
    mems = get_memories(layer="validated", limit=5) or get_memories(layer="core", limit=5)
    return "\n".join([f"- {m['content'][:150]}" for m in mems]) if mems else "No memories yet."


def think() -> str | None:
    raw = call_local(BACKGROUND_PROMPT.format(
        conversation=pick_conversation(),
        memories=get_memory_context(),
        max_length=MAX_THOUGHT_LENGTH,
    ))
    if not raw:
        return None
    if raw.startswith("SEARCH:"):
        question = raw[7:].strip()
        print(f"[BackgroundLoop] Searching: {question[:80]}")
        result = web_search(question)
        if result:
            thought = call_local(WEB_SEARCH_SUMMARY_PROMPT.format(question=question, result=result))
            return f"[web search] {thought}" if thought else None
        return None
    if "NOTHING_NEW" in raw:
        print("[BackgroundLoop] Nothing new this pass.")
        return None
    return raw[:MAX_THOUGHT_LENGTH]


def run_once():
    init_db()
    thought = think()
    if thought:
        mem_id = add_memory(content=thought, layer="recent", source="background_loop")
        print(f"[BackgroundLoop] Saved ({mem_id[:8]}): {thought[:100]}...")
    else:
        print("[BackgroundLoop] No thought generated.")
    for k, v in get_stats().items():
        print(f"  {k}: {v}")


def run_loop():
    init_db()
    print(f"[BackgroundLoop] Started — model: {OLLAMA_MODEL} — every {RUN_INTERVAL_SECONDS}s")
    while True:
        try:
            thought = think()
            if thought:
                mem_id = add_memory(content=thought, layer="recent", source="background_loop")
                print(f"[BackgroundLoop] Saved ({mem_id[:8]}): {thought[:80]}...")
        except Exception as e:
            print(f"[BackgroundLoop] Error: {e}")
        time.sleep(RUN_INTERVAL_SECONDS)


if __name__ == "__main__":
    import sys
    run_once() if "--once" in sys.argv else run_loop()
