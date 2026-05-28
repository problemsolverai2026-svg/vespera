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
    OLLAMA_URL, OLLAMA_MODEL,
    CLOUD_PROVIDER, CLOUD_MODEL, CLOUD_API_KEY,
    COMPLEXITY_THRESHOLD,
)
from memory.store import get_memories, get_recent_conversations

# ─────────────────────────────────────────────
# PROMPTS — personality neutral, user-customizable
# ─────────────────────────────────────────────

COMPLEXITY_CHECK_PROMPT = """You are evaluating whether a user message needs a powerful cloud AI or can be handled locally.

User message: {message}

Score the complexity from 0.0 to 1.0:
- 0.0 = very simple (greetings, casual chat, yes/no)
- 0.5 = moderate (needs memory context, mild reasoning)
- 1.0 = very complex (deep technical, advanced reasoning)

Respond in JSON only:
{{
  "complexity": 0.0,
  "reason": "one short sentence"
}}"""

LOCAL_RESPONSE_PROMPT = """You are a helpful AI assistant with persistent memory.

Your memory of past conversations:
{memories}

Recent conversation:
{recent}

User message: {message}

Respond naturally and helpfully. Keep it concise.
If you're not confident, end your response with: [HANDOFF]"""

CLOUD_CONTEXT_PROMPT = """You are continuing a conversation. Here is the full context:

Persistent memory:
{memories}

Recent conversation:
{recent}

User message: {message}

Respond naturally and helpfully."""


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_context() -> tuple[str, str]:
    mems = get_memories(layer="core", limit=8) or get_memories(layer="validated", limit=8)
    memory_str = "\n".join([f"- {m['content'][:150]}" for m in mems]) if mems else "No memories yet."

    convs = get_recent_conversations(limit=6)
    conv_lines = [f"{c['role'].upper()}: {c['content'][:200]}" for c in reversed(convs)]
    recent_str = "\n".join(conv_lines) if conv_lines else "No recent conversation."

    return memory_str, recent_str


def call_local(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.4, "num_predict": 300}
        }, timeout=60)
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
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

def score_complexity(message: str) -> tuple[float, str]:
    raw = call_local(COMPLEXITY_CHECK_PROMPT.format(message=message))
    if not raw:
        return 0.5, "model unavailable"
    result = parse_json(raw)
    if not result:
        return 0.5, "unparseable"
    return float(result.get("complexity", 0.5)), result.get("reason", "")


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
        return "[Cloud handoff: no API key configured. Set CLOUD_API_KEY in .env]"

    prompt = CLOUD_CONTEXT_PROMPT.format(
        memories=memories, recent=recent, message=message
    )

    # Claude
    if CLOUD_PROVIDER == "claude":
        try:
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
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
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
    complexity, reason = score_complexity(message)
    print(f"[Handoff] Complexity: {complexity:.2f} — {reason}")

    if complexity >= COMPLEXITY_THRESHOLD:
        response = respond_cloud(message, memories, recent)
        return {"response": response, "handled_by": "cloud", "complexity": complexity}

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
