"""
Vespera Background Loop
-----------------------
The persistent thinking engine. Runs continuously in the background.
Lightly reviews past conversations and memories, generates brief thoughts,
and saves them to the 'recent' memory layer for the cleanup crew to review.

When it hits a technical question it doesn't understand, it uses web search
instead of calling the expensive cloud model.

Based on the Persistent Background Thinking Prompt from the Vespera architecture spec.
"""

import os
import json
import time
import random
import requests
from datetime import datetime, timezone
from memory.store import (
    init_db,
    add_memory,
    get_memories,
    get_recent_conversations,
    get_stats,
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

OLLAMA_URL      = "http://localhost:11434/api/generate"
OLLAMA_MODEL    = "llama3.2:3b"     # swap to 7B/13B when ready
RUN_INTERVAL_SECONDS = 180          # think every 3 minutes
MAX_THOUGHT_LENGTH   = 300          # keep thoughts short and focused

# Web search via Venice AI (free, no cloud LLM cost)
VENICE_API_KEY  = os.environ.get("VENICE_API_KEY", "")
VENICE_SEARCH_URL = "https://api.venice.ai/api/v1/augment/search"


# ─────────────────────────────────────────────
# PROMPTS (from Vespera architecture spec)
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
    """Search the web via Venice AI instead of calling cloud LLM."""
    if not VENICE_API_KEY:
        print(f"[BackgroundLoop] No Venice API key — skipping web search")
        return None
    try:
        resp = requests.post(
            VENICE_SEARCH_URL,
            headers={"Authorization": f"Bearer {VENICE_API_KEY}"},
            json={"query": query, "limit": 3},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            return None
        # Combine top results into a short summary
        snippets = [r.get("snippet", "") for r in results[:3] if r.get("snippet")]
        return " ".join(snippets)[:500]
    except Exception as e:
        print(f"[BackgroundLoop] Web search error: {e}")
        return None


# ─────────────────────────────────────────────
# CONTEXT BUILDERS
# ─────────────────────────────────────────────

def pick_conversation() -> str:
    """Pick a conversation to reflect on — mix of recent and older."""
    convs = get_recent_conversations(limit=20)
    if not convs:
        return "No conversations yet."

    # 70% chance recent, 30% chance random older
    if random.random() < 0.7 or len(convs) <= 3:
        selected = convs[:4]  # most recent 4 messages
    else:
        start = random.randint(0, max(0, len(convs) - 4))
        selected = convs[start:start + 4]

    lines = []
    for c in reversed(selected):  # chronological order
        role = c["role"].upper()
        lines.append(f"{role}: {c['content'][:200]}")

    return "\n".join(lines)


def get_memory_context() -> str:
    """Pull a few validated memories for context."""
    mems = get_memories(layer="validated", limit=5)
    if not mems:
        mems = get_memories(layer="core", limit=5)
    if not mems:
        return "No memories yet."

    return "\n".join([f"- {m['content'][:150]}" for m in mems])


# ─────────────────────────────────────────────
# CORE THINKING LOGIC
# ─────────────────────────────────────────────

def think() -> str | None:
    """
    Run one thinking pass.
    Returns the thought generated, or None if nothing new.
    """
    conversation = pick_conversation()
    memories     = get_memory_context()

    prompt = BACKGROUND_PROMPT.format(
        conversation=conversation,
        memories=memories,
        max_length=MAX_THOUGHT_LENGTH,
    )

    raw = call_local_model(prompt)
    if not raw:
        return None

    # Handle web search request
    if raw.startswith("SEARCH:"):
        question = raw[7:].strip()
        print(f"[BackgroundLoop] Technical question — searching: {question[:80]}")
        search_result = web_search(question)

        if search_result:
            summary_prompt = WEB_SEARCH_SUMMARY_PROMPT.format(
                question=question,
                result=search_result,
            )
            thought = call_local_model(summary_prompt)
            if thought:
                return f"[web search] {thought}"
        return None  # search failed, skip this pass

    # Nothing new to say
    if "NOTHING_NEW" in raw:
        print(f"[BackgroundLoop] Nothing new this pass — skipping.")
        return None

    return raw[:MAX_THOUGHT_LENGTH]


# ─────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────

def run_once():
    """Single thinking pass — good for testing."""
    init_db()
    print("[BackgroundLoop] Running single pass...")

    thought = think()
    if thought:
        mem_id = add_memory(
            content=thought,
            layer="recent",
            source="background_loop",
            trust_score=0.0,
        )
        print(f"[BackgroundLoop] Thought saved ({mem_id[:8]}): {thought[:100]}...")
    else:
        print("[BackgroundLoop] No thought generated this pass.")

    print("\n[BackgroundLoop] Stats:")
    for k, v in get_stats().items():
        print(f"  {k}: {v}")


def run_loop():
    """Run background loop continuously."""
    init_db()
    print(f"[BackgroundLoop] Started. Thinking every {RUN_INTERVAL_SECONDS}s.")
    print(f"[BackgroundLoop] Model: {OLLAMA_MODEL}")

    while True:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            print(f"\n[BackgroundLoop] {ts} — thinking...")

            thought = think()
            if thought:
                mem_id = add_memory(
                    content=thought,
                    layer="recent",
                    source="background_loop",
                    trust_score=0.0,
                )
                print(f"[BackgroundLoop] Thought saved ({mem_id[:8]}): {thought[:80]}...")

        except Exception as e:
            print(f"[BackgroundLoop] Error: {e}")

        time.sleep(RUN_INTERVAL_SECONDS)


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
