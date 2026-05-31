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

import requests
from config import COMPONENTS, COMPLEXITY_THRESHOLD
from utils import get_logger, _sanitize
from security import MAX_TOKENS as _MAX_TOKENS

log = get_logger("handoff")

_handoff = COMPONENTS["handoff"]
OLLAMA_URL   = _handoff["ollama_url"]
OLLAMA_MODEL = _handoff["ollama_model"]

_cloud = COMPONENTS["cloud"]
CLOUD_PROVIDER = _cloud["provider"]
CLOUD_MODEL    = _cloud["model"]
CLOUD_API_KEY  = _cloud["api_key"]
CLOUD_BASE_URL = _cloud.get("base_url", "")
MAX_RESPONSE_LENGTH = 2000  # truncate responses before storing to conversation history
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
    memory_str = "\n".join([f"- {_sanitize(m['content'], 150)}" for m in mems]) if mems else "No memories yet."

    convs = get_recent_conversations(limit=20)
    conv_lines = [f"{c['role'].upper()}: {_sanitize(c['content'], 200)}" for c in reversed(convs)]
    recent_str = "\n".join(conv_lines) if conv_lines else "No recent conversation."

    return memory_str, recent_str


def call_local(prompt: str) -> str | None:
    resp = None
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
        log.error("Local model error: %s", e)
        return None
    finally:
        try:
            resp.close()
        except Exception:
            pass


# ─────────────────────────────────────────────
# COMPLEXITY SCORER
# ─────────────────────────────────────────────

def score_complexity(message: str) -> tuple[float, str, bool]:
    from utils import parse_json_response
    raw = call_local(COMPLEXITY_CHECK_PROMPT.format(message=message))
    if not raw:
        return 0.5, "model unavailable", False
    result = parse_json_response(raw)
    if not result:
        return 0.5, "unparseable", False
    try:
        complexity_val = float(result.get("complexity", 0.5))
    except (ValueError, TypeError):
        complexity_val = 0.5
    return complexity_val, result.get("reason", ""), bool(result.get("needs_search", False))


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


def respond_cloud(message: str, memories: str, recent: str, override_prompt: str = None) -> str:
    """
    Hand off to cloud model.
    Currently a placeholder — wire up your preferred cloud API here.
    Supported: claude, grok, venice
    """
    log.info("→ Cloud (%s / %s)", CLOUD_PROVIDER, CLOUD_MODEL)

    if not CLOUD_API_KEY:
        log.warning("No cloud API key — falling back to local.")
        if override_prompt:
            result = call_local(override_prompt)
            return result or (
                "I found web results but need a cloud model to summarize them. "
                "Add CLOUD_API_KEY to your .env for proper search responses."
            )
        response, _ = respond_locally(message, memories, recent)
        return response or (
            "I can answer this better with a cloud AI key. "
            "Add CLOUD_API_KEY to your .env for smarter responses."
        )

    prompt = override_prompt if override_prompt is not None else CLOUD_CONTEXT_PROMPT.format(
        memories=memories, recent=recent, message=message
    )

    # Claude
    if CLOUD_PROVIDER == "claude":
        try:
            messages = [{"role": "user", "content": prompt}]
            # Tool loop — Claude can call tools multiple times
            for _ in range(10):  # max 10 tool calls per response
                resp = None
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
                            "max_tokens": _MAX_TOKENS,
                            "tools": TOOL_DEFINITIONS,
                            "messages": messages,
                        },
                        timeout=60,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                finally:
                    try:
                        if resp: resp.close()
                    except Exception:
                        pass
                stop_reason = data.get("stop_reason")

                # No tool call — return the text
                if stop_reason == "end_turn":
                    for block in (data.get("content") or []):
                        if block.get("type") == "text":
                            return block["text"]
                    log.warning("Claude returned end_turn with no text block: %s", data.get("content"))
                    return "[No response received from cloud model]"

                # Partial response — max tokens reached, return what we have
                if stop_reason == "max_tokens":
                    for block in (data.get("content") or []):
                        if block.get("type") == "text":
                            return block["text"] + " [truncated]"
                    return "[Response truncated — max tokens reached]"

                # Tool use — run the tool and loop
                if stop_reason == "tool_use":
                    content_blocks = data.get("content") or []
                    messages.append({"role": "assistant", "content": content_blocks})
                    tool_results = []
                    for block in content_blocks:
                        if block.get("type") == "tool_use":
                            result = run_tool(block["name"], block.get("input", {}))
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result,
                            })
                    if not tool_results:
                        log.warning("Claude stop_reason=tool_use but no tool_use blocks in response")
                        for block in content_blocks:
                            if block.get("type") == "text":
                                return block["text"]
                        return "[Error: unexpected stop — tool_use signaled but no tool blocks found]"
                    messages.append({"role": "user", "content": tool_results})
                    continue

                # Unknown stop reason — return whatever text is present, else descriptive error
                for block in (data.get("content") or []):
                    if block.get("type") == "text":
                        return block["text"]
                log.warning("Claude unexpected stop_reason=%r — content: %s", stop_reason, data.get("content"))
                return f"[Cloud error: unexpected stop_reason '{stop_reason}']"
            log.error("Claude tool call loop exhausted after 10 iterations")
            return "[I ran into an issue with the cloud model. Please try again.]"
        except Exception as e:
            log.error("Claude cloud error: %s", e)
            return "[I ran into an issue reaching the cloud model. Please try again.]"

    # Venice (OpenAI-compatible)
    if CLOUD_PROVIDER == "venice":
        resp = None
        try:
            resp = requests.post(
                "https://api.venice.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {CLOUD_API_KEY}"},
                json={
                    "model": CLOUD_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": _MAX_TOKENS,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if not content:
                raise ValueError("Empty response from Venice")
            return content
        except Exception as e:
            log.error("Venice cloud error: %s", e)
            return "[I ran into an issue reaching the cloud model. Please try again.]"
        finally:
            try:
                if resp: resp.close()
            except Exception:
                pass

    # Groq (OpenAI-compatible)
    if CLOUD_PROVIDER == "groq":
        resp = None
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {CLOUD_API_KEY}"},
                json={
                    "model": CLOUD_MODEL or "llama3-8b-8192",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": _MAX_TOKENS,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if not content:
                raise ValueError("Empty response from Groq")
            return content
        except Exception as e:
            log.error("Groq cloud error: %s", e)
            return "[I ran into an issue reaching the cloud model. Please try again.]"
        finally:
            try:
                if resp: resp.close()
            except Exception:
                pass

    # OpenAI
    if CLOUD_PROVIDER == "openai":
        resp = None
        try:
            _openai_base = CLOUD_BASE_URL.rstrip("/") if CLOUD_BASE_URL else "https://api.openai.com/v1"
            resp = requests.post(
                f"{_openai_base}/chat/completions",
                headers={"Authorization": f"Bearer {CLOUD_API_KEY}"},
                json={
                    "model": CLOUD_MODEL or "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": _MAX_TOKENS,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
            if not content:
                raise ValueError("Empty response from OpenAI")
            return content
        except Exception as e:
            log.error("OpenAI cloud error: %s", e)
            return "[I ran into an issue reaching the cloud model. Please try again.]"
        finally:
            try:
                if resp: resp.close()
            except Exception:
                pass

    # Gemini
    if CLOUD_PROVIDER == "gemini":
        resp = None
        try:
            from urllib.parse import quote
            model = CLOUD_MODEL or "gemini-1.5-flash"
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe='')}:generateContent",
                headers={"x-goog-api-key": CLOUD_API_KEY},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": _MAX_TOKENS},
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            try:
                content = data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError, TypeError):
                raise ValueError(f"Unexpected Gemini response format: {list(data.keys())}")
            return content
        except Exception as e:
            log.error("Gemini cloud error: %s", e)
            return "[I ran into an issue reaching the cloud model. Please try again.]"
        finally:
            try:
                if resp: resp.close()
            except Exception:
                pass

    return f"[Cloud provider '{CLOUD_PROVIDER}' not supported. Use: claude, groq, openai, gemini, venice]"


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

def handle_message(message: str) -> dict:
    memories, recent = get_context()
    complexity, reason, needs_search = score_complexity(message)
    log.info("Complexity: %.2f | search: %s — %s", complexity, needs_search, reason)

    # Web search first for real-time questions — always synthesize with cloud
    if needs_search:
        results = web_search(message)
        if results:
            from datetime import datetime
            today = datetime.now().strftime("%A, %B %d, %Y")
            formatted_prompt = SEARCH_RESPONSE_PROMPT.format(today=today, results=results, message=message)
            response = respond_cloud(message, memories, recent, override_prompt=formatted_prompt)
            return {"response": response[:MAX_RESPONSE_LENGTH], "handled_by": "search+cloud", "complexity": complexity}

    # Complex reasoning — cloud if available, else local
    if complexity >= COMPLEXITY_THRESHOLD:
        if CLOUD_API_KEY:
            response = respond_cloud(message, memories, recent)
            return {"response": response[:MAX_RESPONSE_LENGTH], "handled_by": "cloud", "complexity": complexity}
        # No cloud key — try local
        response, _ = respond_locally(message, memories, recent)
        if not response:
            response = "I'm having trouble reaching my local model right now. Please check that Ollama is running."
        return {"response": response[:MAX_RESPONSE_LENGTH], "handled_by": "local", "complexity": complexity}

    # Simple — local model
    response, needs_handoff = respond_locally(message, memories, recent)
    if needs_handoff:
        response = respond_cloud(message, memories, recent)
        return {"response": response[:MAX_RESPONSE_LENGTH], "handled_by": "cloud", "complexity": complexity}

    return {"response": response[:MAX_RESPONSE_LENGTH], "handled_by": "local", "complexity": complexity}


if __name__ == "__main__":
    from memory.store import init_db
    init_db()
    tests = [
        "Hey, how are you?",
        "What did we talk about with the persistent loop?",
        "Explain transformer attention vs recurrent neural networks in detail.",
    ]
    for msg in tests:
        log.info("USER: %s", msg)
        result = handle_message(msg)
        log.info("HANDLED BY: %s (%.2f) | %s", result['handled_by'], result['complexity'], result['response'][:150])
