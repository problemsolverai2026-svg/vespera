"""
Vespera Background Loop
-----------------------
The persistent thinking engine. Runs continuously in the background.
Lightly reviews past conversations and memories, generates brief thoughts,
and saves them to the 'recent' memory layer for the cleanup crew to review.

When it hits a technical question it doesn't understand, it uses web search
instead of calling the expensive cloud model.
"""

import json
import time
import random
import requests
from datetime import datetime, timezone
from config import (
    OLLAMA_URL, OLLAMA_MODEL,
    VENICE_API_KEY, VENICE_SEARCH_URL,
    BACKGROUND_LOOP_INTERVAL as RUN_INTERVAL_SECONDS,
    MAX_THOUGHT_LENGTH,
)
from memory.store import (
    init_db, add_memory, get_memories, get_recent_conversations, get_stats,
)

# ─────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────

BACKGROUND_PROMPT = """You are a persistent AI memory system. Your job is to lightly review past conversations and generate one brief, focused thought.

Focus ONLY on technical concepts and ideas. Ignore emotions.

Past conversation to review:
{conversation}

Recent memories for context:
{memories}

Your task:
1. Identify the core technical idea in this conversation
2. Check if there is anything you don't fully understand
3. If you don't understand something technical, say: SEARCH: <your question>
4. Otherwise, write ONE short thought (2-3 sentences max) about what you understood

Rules:
- Stay strictly technical
- Do NOT repeat what was already said — add a new angle or connection
- If you have nothing new to add, say: NOTHING_NEW
- Keep it under {max_length} characters"""

WEB_SEARCH_SUMMARY_PROMPT = """You found this information from a web search:

Question: {question}
Search result: {result}

Summarize what you learned in 1-2 sentences, technically focused."""


# ─────────────────────────────────────────────
# MODEL + SEARCH
# ─────────────────────────────────────────────

def call_local_model(prompt: str, timeout: int = 60) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 200}
        }, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        print(f"[BackgroundLoop] Local model error: {e}")
        return None


def web_search(query: str) -> str | None:
    if not VENICE_API_KEY:
        print("[BackgroundLoop] No Venice API key — skipping web search")
        return None
    try:
        resp = requests.post(
            VENICE_SEARCH_URL,
            headers={"Authorization": f"Bearer {VENICE_API_KEY}"},
            json={"query": query, "limit": 3},
            timeout=15,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        snippets = [r.get("snippet", "") for r in results[:3] if r.get("snippet")]
        return " ".join(snippets)[:500] if snippets else None
    except Exception as e:
        print(f"[BackgroundLoop] Web search error: {e}")
        return None


# ─────────────────────────────────────────────
# CONTEXT BUILDERS
# ─────────────────────────────────────────────

def pick_conversation() -> str:
    convs = get_recent_conversations(limit=20)
    if not convs:
        return "No conversations yet."
    if random.random() < 0.7 or len(convs) <= 3:
        selected = convs[:4]
    else:
        start = random.randint(0, max(0, len(convs) - 4))
        selected = convs[start:start + 4]
    lines = [f"{c['role'].upper()}: {c['content'][:200]}" for c in reversed(selected)]
    return "\n".join(lines)


def get_memory_context() -> str:
    mems = get_memories(layer="validated", limit=5) or get_memories(layer="core", limit=5)
    if not mems:
        return "No memories yet."
    return "\n".join([f"- {m['content'][:150]}" for m in mems])


# ─────────────────────────────────────────────
# CORE THINKING LOGIC
# ─────────────────────────────────────────────

def think() -> str | None:
    prompt = BACKGROUND_PROMPT.format(
        conversation=pick_conversation(),
        memories=get_memory_context(),
        max_length=MAX_THOUGHT_LENGTH,
    )
    raw = call_local_model(prompt)
    if not raw:
        return None

    if raw.startswith("SEARCH:"):
        question = raw[7:].strip()
        print(f"[BackgroundLoop] Searching: {question[:80]}")
        result = web_search(question)
        if result:
            thought = call_local_model(WEB_SEARCH_SUMMARY_PROMPT.format(
                question=question, result=result
            ))
            return f"[web search] {thought}" if thought else None
        return None

    if "NOTHING_NEW" in raw:
        print("[BackgroundLoop] Nothing new this pass.")
        return None

    return raw[:MAX_THOUGHT_LENGTH]


# ─────────────────────────────────────────────
# RUNNERS
# ─────────────────────────────────────────────

def run_once():
    init_db()
    print("[BackgroundLoop] Running single pass...")
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
    print(f"[BackgroundLoop] Started — {OLLAMA_MODEL} — every {RUN_INTERVAL_SECONDS}s")
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
