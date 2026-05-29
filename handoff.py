"""
Vespera Handoff Logic
---------------------
Decides whether the local model handles a user message itself,
or hands off to the cloud model.

Hand off to cloud if:
  - Message is complex or technical
  - Local model signals uncertainty ([HANDOFF])

Handle locally if:
  - Message is casual or simple
  - Local model has clear context from memory
"""

import json
import requests
from config import (
    COMPONENTS,
    COMPLEXITY_THRESHOLD,
)

_handoff = COMPONENTS["handoff"]
OLLAMA_URL   = _handoff["ollama_url"]
OLLAMA_MODEL = _handoff["ollama_model"]

_cloud = COMPONENTS["cloud"]
CLOUD_PROVIDER = _cloud["provider"]
CLOUD_MODEL    = _cloud["model"]
CLOUD_API_KEY  = _cloud["api_key"]
from memory.store import get_memories, get_recent_conversations
from web_search import search as web_search
from tools import TOOL_DEFINITIONS, run_tool

# ─────────────────────────────────────────────
# PROMPTS — personality neutral, user-customizable
# ─────────────────────────────────────────────

COMPLEXITY_CHECK_PROMPT = """You are evaluating whether a user message needs a powerful cloud AI or can be handled locally.

User message: {message}

Score the complexity AND whether it needs a web search:
- 0.0 = very simple (greetings, casual chat, yes/no)
- 0.5 = moderate (needs memory context, mild reasoning)
- 0.8 = current events, news, real-time info, anything that happened recently
- 1.0 = very complex (deep technical, advanced reasoning, coding, math)

needs_search = true if the question asks about: news, current events, prices, weather, anything happening today/recently/now.

Respond in JSON only:
{{
  "complexity": 0.0,
  "needs_search": false,
  "reason": "one short sentence"
}}"""

LOCAL_RESPONSE_PROMPT = """You are a helpful AI assistant with persistent memory. Be direct and concise. No filler phrases like "great question" or "I hope you're having a great day."

Your memory of past conversations:
{memories}

Recent conversation history (read this carefully before answering):
{recent}

User message: {message}

Answer using the conversation history above when relevant. If the answer is clearly in the history, use it. Only add [HANDOFF] at the very end if the question requires real-time data, advanced coding, or deep technical reasoning you genuinely cannot answer. Simple questions and greetings should NEVER use [HANDOFF]."""

CLOUD_CONTEXT_PROMPT = """You are a helpful AI assistant. Answer the user's message directly and helpfully.

Do not mention being updated, restarted, or any system changes. Do not reference the context below unless it is directly relevant to the user's question.

Memory context:
{memories}

Recent conversation:
{recent}

User message: {message}

Respond naturally and concisely. Get straight to the answer."""

SEARCH_RESPONSE_PROMPT = """You are a helpful AI assistant. Answer the user's question using the search results below.

Today's date: {today}

Search results:
{results}

User message: {message}

Answer directly based on the search results. Use today's date to determine what is recent or current. If the results don't contain enough info, say so clearly. Keep it concise."""


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_context() -> tuple[str, str]:
    mems = get_memories(layer="core", limit=8) or get_memories(layer="validated", limit=8)
    memory_str = "\n".join([f"- {m['content'][:150]}" for m in mems]) if mems else "No memories yet."

    convs = get_recent_conversations(limit=20)
    conv_lines = [f"{c['role'].upper()}: {c['content'][:200]}" for c in reversed(convs)]
    recent_str = "\n".join(conv_lines) if conv_lines else "No recent conversation."

    return memory_str, recent_str


def call_local(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 300}
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        # /api/chat returns message.content, /api/generate returns response
        return (data.get("message", {}).get("content") or data.get("response", "")).strip()
    except Exception as e:
        print(f"[Handoff] Local model error: {e}")
        return None


def parse_json(raw: str) -> dict | None:
    try:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        return json.loads(raw[start:end])
    except Exception:
        return None


# ─────────────────────────────────────────────
# COMPLEXITY SCORER
# ─────────────────────────────────────────────

def score_complexity(message: str) -> tuple[float, str, bool]:
    raw = call_local(COMPLEXITY_CHECK_PROMPT.format(message=message))
    if not raw:
        return 0.5, "model unavailable", False
    result = parse_json(raw)
    if not result:
        return 0.5, "unparseable", False
    return float(result.get("complexity", 0.5)), result.get("reason", ""), bool(result.get("needs_search", False))


# ─────────────────────────────────────────────
# RESPONSE HANDLERS
# ─────────────────────────────────────────────

def respond_locally(message: str, memories: str, recent: str) -> tuple[str, bool]:
    raw = call_local(LOCAL_RESPONSE_PROMPT.format(
        memories=memories, recent=recent, message=message
    ))
    if not raw:
        return "", True
    if "[HANDOFF]" in raw:
        return raw.replace("[HANDOFF]", "").strip(), True
    return raw, False


def respond_cloud(message: str, memories: str, recent: str) -> str:
    """
    Hand off to cloud model.
    Currently a placeholder — wire up your preferred cloud API here.
    Supported: claude, grok, venice
    """
    print(f"[Handoff] → Cloud ({CLOUD_PROVIDER} / {CLOUD_MODEL})")

    if not CLOUD_API_KEY:
        print("[Handoff] No cloud API key — falling back to local model.")
        response, _ = respond_locally(message, memories, recent)
        return response or "I can answer this better with a cloud AI key. Add CLOUD_API_KEY to your .env for smarter responses."

    prompt = CLOUD_CONTEXT_PROMPT.format(
        memories=memories, recent=recent, message=message
    )

    # Claude
    if CLOUD_PROVIDER == "claude":
        try:
            messages = [{"role": "user", "content": prompt}]
            # Tool loop — Claude can call tools multiple times
            for _ in range(10):  # max 10 tool calls per response
                resp = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": CLOUD_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": CLOUD_MODEL,
                        "max_tokens": 1024,
                        "tools": TOOL_DEFINITIONS,
                        "messages": messages,
                    },
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                stop_reason = data.get("stop_reason")

                # No tool call — return the text
                if stop_reason == "end_turn":
                    for block in data["content"]:
                        if block.get("type") == "text":
                            return block["text"]
                    return ""

                # Tool use — run the tool and loop
                if stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": data["content"]})
                    tool_results = []
                    for block in data["content"]:
                        if block.get("type") == "tool_use":
                            result = run_tool(block["name"], block.get("input", {}))
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result,
                            })
                    messages.append({"role": "user", "content": tool_results})
                    continue

                break  # unknown stop reason
            return "[Error: tool loop exhausted]"
        except Exception as e:
            return f"[Cloud error: {e}]"

    # Venice (OpenAI-compatible)
    if CLOUD_PROVIDER == "venice":
        try:
            resp = requests.post(
                "https://api.venice.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {CLOUD_API_KEY}"},
                json={
                    "model": CLOUD_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"[Cloud error: {e}]"

    return f"[Cloud provider '{CLOUD_PROVIDER}' not yet implemented]"


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

def handle_message(message: str) -> dict:
    memories, recent = get_context()
    complexity, reason, needs_search = score_complexity(message)
    print(f"[Handoff] Complexity: {complexity:.2f} | search: {needs_search} — {reason}")

    # Web search first for real-time questions — always synthesize with cloud
    if needs_search:
        results = web_search(message)
        if results:
            from datetime import datetime
            today = datetime.now().strftime("%A, %B %d, %Y")
            cloud_msg = f"Today is {today}.\n\nUser question: {message}\n\nSearch results:\n{results}"
            response = respond_cloud(cloud_msg, memories, recent)
            return {"response": response, "handled_by": "search+cloud", "complexity": complexity}

    # Complex reasoning — cloud if available, else local
    if complexity >= COMPLEXITY_THRESHOLD:
        if CLOUD_API_KEY:
            response = respond_cloud(message, memories, recent)
            return {"response": response, "handled_by": "cloud", "complexity": complexity}
        # No cloud key — try local
        response, _ = respond_locally(message, memories, recent)
        return {"response": response, "handled_by": "local", "complexity": complexity}

    # Simple — local model
    response, needs_handoff = respond_locally(message, memories, recent)
    if needs_handoff:
        response = respond_cloud(message, memories, recent)
        return {"response": response, "handled_by": "cloud", "complexity": complexity}

    return {"response": response, "handled_by": "local", "complexity": complexity}


if __name__ == "__main__":
    from memory.store import init_db
    init_db()
    tests = [
        "Hey, how are you?",
        "What did we talk about with the persistent loop?",
        "Explain transformer attention vs recurrent neural networks in detail.",
    ]
    for msg in tests:
        print(f"\n{'='*50}\nUSER: {msg}")
        result = handle_message(msg)
        print(f"HANDLED BY: {result['handled_by']} ({result['complexity']:.2f})")
        print(f"RESPONSE: {result['response'][:150]}")
