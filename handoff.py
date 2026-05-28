"""
Vespera Handoff Logic
---------------------
Decides whether the local model handles a user message itself,
or hands off to the cloud model (Claude/Grok).

Rules (from Vespera architecture spec):
  Hand off to cloud if:
  - Message is highly technical or complex
  - Local model is uncertain how to reply naturally
  - Topic requires significantly better reasoning

  Handle locally if:
  - Message is casual, simple, or conversational
  - Local model has clear context from memory

Local model responds first. If it signals uncertainty or the
message scores above the complexity threshold, cloud takes over.
"""

import json
import requests
from memory.store import get_memories, get_recent_conversations

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

OLLAMA_URL      = "http://localhost:11434/api/generate"
OLLAMA_MODEL    = "llama3.2:3b"
COMPLEXITY_THRESHOLD = 0.65       # above this → cloud handles it

# Cloud model config (fill in when ready to test)
CLOUD_PROVIDER  = "claude"        # claude | grok | venice
CLOUD_API_KEY   = ""              # set when testing
CLOUD_MODEL     = "claude-sonnet-4-5"


# ─────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────

COMPLEXITY_CHECK_PROMPT = """You are evaluating whether a user message needs a powerful cloud AI or can be handled locally.

User message: {message}

Score the complexity from 0.0 to 1.0:
- 0.0 = very simple (greetings, casual chat, simple yes/no questions)
- 0.5 = moderate (needs memory context, mild reasoning)
- 1.0 = very complex (deep technical, advanced reasoning, nuanced analysis)

Respond in JSON only:
{{
  "complexity": 0.0 to 1.0,
  "reason": "one short sentence"
}}"""


LOCAL_RESPONSE_PROMPT = """You are a warm, helpful personal AI assistant with persistent memory.

Your memory of past conversations:
{memories}

Recent conversation:
{recent}

User message: {message}

Respond naturally and helpfully. Keep it concise.
If you're not confident in your answer, end your response with: [HANDOFF]"""


CLOUD_CONTEXT_PROMPT = """You are continuing a conversation. Here is the context:

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
    """Pull memory and recent conversation context."""
    mems = get_memories(layer="core", limit=8)
    if not mems:
        mems = get_memories(layer="validated", limit=8)

    memory_str = "\n".join([f"- {m['content'][:150]}" for m in mems]) if mems else "No memories yet."

    convs = get_recent_conversations(limit=6)
    conv_lines = []
    for c in reversed(convs):
        conv_lines.append(f"{c['role'].upper()}: {c['content'][:200]}")
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
    """Score how complex a message is. Returns (score, reason)."""
    prompt = COMPLEXITY_CHECK_PROMPT.format(message=message)
    raw = call_local(prompt)

    if not raw:
        return 0.5, "model unavailable — defaulting to moderate"

    result = parse_json(raw)
    if not result:
        return 0.5, "unparseable — defaulting to moderate"

    score = float(result.get("complexity", 0.5))
    reason = result.get("reason", "")
    return score, reason


# ─────────────────────────────────────────────
# RESPONSE HANDLERS
# ─────────────────────────────────────────────

def respond_locally(message: str, memories: str, recent: str) -> tuple[str, bool]:
    """
    Try to respond locally.
    Returns (response, needs_handoff).
    """
    prompt = LOCAL_RESPONSE_PROMPT.format(
        memories=memories,
        recent=recent,
        message=message,
    )
    raw = call_local(prompt)
    if not raw:
        return "", True

    if "[HANDOFF]" in raw:
        response = raw.replace("[HANDOFF]", "").strip()
        return response, True

    return raw, False


def respond_cloud(message: str, memories: str, recent: str) -> str:
    """
    Hand off to cloud model.
    Returns the cloud model's response.
    """
    # When real cloud integration is wired up, this calls Claude/Grok/Venice.
    # For now returns a clear placeholder so the system is testable end-to-end.
    prompt = CLOUD_CONTEXT_PROMPT.format(
        memories=memories,
        recent=recent,
        message=message,
    )
    print(f"[Handoff] → Cloud model ({CLOUD_MODEL})")
    # TODO: wire up actual cloud API call here
    return f"[CLOUD RESPONSE PLACEHOLDER — wire up {CLOUD_PROVIDER} API here]\nPrompt sent:\n{prompt[:200]}..."


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

def handle_message(message: str) -> dict:
    """
    Main handoff function.
    Takes a user message, returns a dict with response + metadata.
    """
    memories, recent = get_context()

    # Step 1: score complexity
    complexity, complexity_reason = score_complexity(message)
    print(f"[Handoff] Complexity: {complexity:.2f} — {complexity_reason}")

    # Step 2: if complex, go straight to cloud
    if complexity >= COMPLEXITY_THRESHOLD:
        print(f"[Handoff] Complex message → cloud")
        response = respond_cloud(message, memories, recent)
        return {
            "response": response,
            "handled_by": "cloud",
            "complexity": complexity,
            "reason": complexity_reason,
        }

    # Step 3: try local first
    response, needs_handoff = respond_locally(message, memories, recent)

    if needs_handoff:
        print(f"[Handoff] Local uncertain → cloud")
        response = respond_cloud(message, memories, recent)
        return {
            "response": response,
            "handled_by": "cloud",
            "complexity": complexity,
            "reason": "local model requested handoff",
        }

    return {
        "response": response,
        "handled_by": "local",
        "complexity": complexity,
        "reason": complexity_reason,
    }


# ─────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    from memory.store import init_db
    init_db()

    test_messages = [
        "Hey, how are you?",
        "What did we talk about with the persistent loop?",
        "Explain the difference between transformer attention and recurrent neural networks in detail.",
    ]

    for msg in test_messages:
        print(f"\n{'='*60}")
        print(f"USER: {msg}")
        result = handle_message(msg)
        print(f"HANDLED BY: {result['handled_by']} (complexity {result['complexity']:.2f})")
        print(f"RESPONSE: {result['response'][:200]}")
