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

import os
import re
import requests
from pathlib import Path
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

def _trim(text: str) -> str:
    """Trim to MAX_RESPONSE_LENGTH with an indicator so the frontend knows it was cut."""
    if len(text) <= MAX_RESPONSE_LENGTH:
        return text
    return text[:MAX_RESPONSE_LENGTH - 20].rstrip() + "\n\n[response truncated]"
from memory.store import get_memories, get_recent_conversations, get_followups, mark_followup_used
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

CRITICAL: Only respond using information explicitly present in the memory and conversation history below. If something is not in your memory or the conversation, say "I don't know" or "I don't have that information." Never invent, assume, or fabricate names, projects, dates, or context that are not explicitly stated below.

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
    # Exclude follow-up questions from context — they surface only via re-engagement,
    # not via the regular memory context (prevents them repeating every conversation).
    def _not_followup(mems):
        return [m for m in mems if m.get("source") != "followup"]

    mems = _not_followup(get_memories(layer="core", limit=5)) or \
           _not_followup(get_memories(layer="validated", limit=5))
    memory_str = "\n".join([f"- {_sanitize(m['content'], 120)}" for m in mems]) if mems else "No memories yet."

    # Limit to 6 recent turns — llama3.2:3b has ~2K context; 20 turns easily overflows it.
    # Cloud models get full context via CLOUD_CONTEXT_PROMPT when complexity routes there.
    convs = get_recent_conversations(limit=6)
    conv_lines = [f"{c['role'].upper()}: {_sanitize(c['content'], 150)}" for c in reversed(convs)]
    recent_str = "\n".join(conv_lines) if conv_lines else "No recent conversation."

    return memory_str, recent_str


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.4, num_predict=300)


# ─────────────────────────────────────────────
# COMPLEXITY SCORER
# ─────────────────────────────────────────────

# Price keywords for pre-check — avoids losing price queries when Ollama is slow/down.
# Deliberately narrow: only unambiguous financial phrases paired with asset names.
# "cost" and "worth" are intentionally excluded — too many false positives.
_ASSETS = r"(silver|gold|bitcoin|btc|ethereum|eth|crude oil|oil|natural gas|nasdaq|dow jones|dow|djia|s&p 500|s&p|sp500|copper|platinum|palladium)"
_PRICE_PRE_CHECK = re.compile(
    r"\b(price|spot price|per ounce|per share|trading at|market price)\b.*" + _ASSETS
    + r"|" + _ASSETS + r".*\b(price|spot price|per ounce|per share|trading at|market price)\b",
    re.IGNORECASE,
)


def score_complexity(message: str) -> tuple[float, str, bool]:
    from utils import parse_json_response
    # Quick pre-check: force needs_search for obvious price queries before hitting Ollama
    if _PRICE_PRE_CHECK.search(message):
        return 0.8, "price query detected", True
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
    # Re-read cloud config on every call so .env changes via the UI apply
    # without requiring a full Vespera restart.
    try:
        from dotenv import load_dotenv as _ldenv
        _ldenv(Path(__file__).parent / ".env", override=True)
    except ImportError:
        pass
    CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", _cloud.get("provider", "groq"))
    CLOUD_MODEL    = os.getenv("CLOUD_MODEL",    _cloud.get("model",    "llama3-8b-8192"))
    CLOUD_API_KEY  = os.getenv("CLOUD_API_KEY",  "")
    CLOUD_BASE_URL = os.getenv("CLOUD_BASE_URL", _cloud.get("base_url", ""))

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
                        if resp is not None: resp.close()
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
                            # Cap tool result to avoid context overflow across multiple calls
                            if len(result) > 8000:
                                result = result[:8000] + "\n[tool output truncated]"
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
                    import time as _t
                    _t.sleep(min(0.5 * (2 ** min(_, 4)), 10))  # exponential backoff capped at 10s
                    continue

                # Unknown stop reason — return whatever text is present, else descriptive error
                for block in (data.get("content") or []):
                    if block.get("type") == "text":
                        return block["text"]
                log.warning("Claude unexpected stop_reason=%r — content: %s", stop_reason, data.get("content"))
                return "[I ran into an issue with the cloud model. Please try again.]"
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
                if resp is not None: resp.close()
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
                if resp is not None: resp.close()
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
                if resp is not None: resp.close()
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
                if resp is not None: resp.close()
            except Exception:
                pass

    return f"[Cloud provider '{CLOUD_PROVIDER}' not supported. Use: claude, groq, openai, gemini, venice]"


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

# Gap threshold: if the last conversation was more than this many minutes ago,
# treat the current message as the start of a new session and surface a follow-up.
_SESSION_GAP_MINUTES = int(os.getenv("VESPERA_SESSION_GAP_MINUTES", "30"))


def _is_new_session() -> bool:
    """Return True if enough time has passed since the last conversation to treat this as a new session."""
    try:
        from datetime import datetime, timezone
        convs = get_recent_conversations(limit=2)
        if not convs:
            return False  # no history = first ever message, not a return
        # convs are newest-first; first entry is most recent
        last_ts_str = convs[0].get("timestamp", "")
        if not last_ts_str:
            return False
        last_ts = datetime.fromisoformat(last_ts_str)
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)
        gap = (datetime.now(timezone.utc) - last_ts).total_seconds() / 60
        return gap >= _SESSION_GAP_MINUTES
    except Exception:
        return False


def _get_reengagement_suffix() -> tuple[str, str | None]:
    """Return (suffix_text, followup_memory_id) if there's a pending follow-up to surface.
    Returns ('', None) if nothing to surface.
    """
    try:
        followups = get_followups(limit=3)
        if not followups:
            return "", None
        # Pick the most recent follow-up
        f = followups[0]
        return f"\n\n{f['content']}", f["id"]
    except Exception:
        return "", None


_REMINDER_PRE_CHECK = re.compile(
    r"\b(remind|reminder|set a reminder|set reminder|alert me|notify me)\b",
    re.IGNORECASE,
)
_LIST_REMINDERS_CHECK = re.compile(
    r"\b(list|show|what are|my) reminders?\b",
    re.IGNORECASE,
)
_CANCEL_REMINDER_CHECK = re.compile(
    r"\b(cancel|delete|remove) reminder\b",
    re.IGNORECASE,
)

# Note-taking patterns
# Matches the trigger phrase anywhere in the message; group(1) = everything after it
_NOTE_SAVE_CHECK = re.compile(
    r"(?i)^(?:.*?\b)?(?:"
    r"note(?:\s+to\s+self)?|jot(?:\s+down)?|save(?:\s+a)?\s+note|quick\s+note"
    r"|take(?:\s+a)?\s+note|make(?:\s+a)?\s+note(?:\s+of\s+this)?"
    r"|remember(?:\s+that|\s+to)?"
    r"|i\s+need\s+(?:you\s+to\s+)?(?:make|take|save|write|note)(?:\s+a)?\s*(?:note(?:\s+of\s+this)?|this\s+down|this)?"
    r"|can\s+you\s+(?:take|make|save|note|write(?:\s+down)?|jot(?:\s+down)?)"
    r"|please\s+(?:note|save|remember|write(?:\s+down)?|jot(?:\s+down)?)"
    r"|write(?:\s+this)?(?:\s+down)?|save\s+this"
    r")\s*[:\-]?\s*"
)
_NOTE_LIST_CHECK = re.compile(
    r"\b(?:show|list|what(?:'s| are| were)?|my|get|read)\s+(?:my\s+)?notes?\b",
    re.IGNORECASE,
)
_NOTE_DELETE_CHECK = re.compile(
    r"\b(?:delete|remove|clear|erase)\s+note\b",
    re.IGNORECASE,
)

# Photo command patterns
_PHOTO_LIST_CHECK = re.compile(
    r"\b(?:show|list|my|get|what(?:'s| are)?)\s+(?:my\s+)?photos?\b",
    re.IGNORECASE,
)
_PHOTO_DELETE_CHECK = re.compile(
    r"\b(?:delete|remove|erase)\s+photo\b",
    re.IGNORECASE,
)


def _handle_reminder_locally(message: str) -> dict | None:
    """Intercept reminder requests and handle them with the local model. Returns result dict or None."""

    # List reminders
    if _LIST_REMINDERS_CHECK.search(message):
        from tools import run_list_reminders
        return {"response": run_list_reminders(), "handled_by": "local-reminder", "complexity": 0.0}

    # Cancel reminder
    if _CANCEL_REMINDER_CHECK.search(message):
        match = re.search(r'[0-9a-f-]{8,}', message, re.IGNORECASE)
        if match:
            from tools import run_cancel_reminder
            return {"response": run_cancel_reminder(match.group(0)), "handled_by": "local-reminder", "complexity": 0.0}
        return {"response": "Which reminder? Say 'list reminders' to see IDs.", "handled_by": "local-reminder", "complexity": 0.0}

    # Set reminder — use scheduler's local parser
    if _REMINDER_PRE_CHECK.search(message):
        try:
            from scheduler import parse_reminder, add_reminder
            from zoneinfo import ZoneInfo
            parsed = parse_reminder(message)
            if parsed:
                rid = add_reminder(parsed["message"], parsed["fire_at"], recur=parsed.get("recur"))
                tz = ZoneInfo(os.getenv("VESPERA_TIMEZONE", "America/Chicago"))
                fire_local = parsed["fire_at"].astimezone(tz)
                dt_str = fire_local.strftime("%A, %B %d at %I:%M %p %Z").replace(" 0", " ")
                recur_note = f" (repeats {parsed['recur']})" if parsed.get("recur") else ""
                return {"response": f"Got it — I'll remind you to {parsed['message']} on {dt_str}{recur_note}.", "handled_by": "local-reminder", "complexity": 0.0}
            else:
                return {"response": "I couldn't parse the time for that reminder. Try something like 'remind me to call John at 8pm'.", "handled_by": "local-reminder", "complexity": 0.0}
        except Exception as e:
            log.warning("Local reminder handling failed: %s", e)
            return None

    return None


def _handle_note_locally(message: str) -> dict | None:
    """Intercept note-taking requests and handle them directly. Returns result dict or None."""
    from notes import add_note, list_notes, delete_note, init_notes_db
    init_notes_db()

    # Save a note
    m = _NOTE_SAVE_CHECK.match(message)
    if m:
        content = message[m.end():].strip()
        if not content:
            return {"response": "What do you want to note? Try: 'note: pick up milk'", "handled_by": "local-note", "complexity": 0.0}
        note = add_note(content)
        short_id = note["id"][:8]
        return {"response": f"\U0001f4dd Noted: {content} (id: {short_id})", "handled_by": "local-note", "complexity": 0.0}

    # List notes
    if _NOTE_LIST_CHECK.search(message):
        notes = list_notes()
        if not notes:
            return {"response": "No notes saved yet. Say 'note: something' to add one.", "handled_by": "local-note", "complexity": 0.0}
        from zoneinfo import ZoneInfo
        from datetime import datetime, timezone
        tz = ZoneInfo(os.getenv("VESPERA_TIMEZONE", "America/Chicago"))
        lines = []
        for i, n in enumerate(notes, 1):
            try:
                dt = datetime.fromisoformat(n["created_at"]).astimezone(tz)
                date_str = dt.strftime("%b %d %I:%M %p")
            except Exception:
                date_str = ""
            lines.append(f"{i}. [{n['id'][:8]}] {n['content']}  ({date_str})")
        return {"response": "\U0001f4cb Your notes:\n" + "\n".join(lines), "handled_by": "local-note", "complexity": 0.0}

    # Delete a note
    if _NOTE_DELETE_CHECK.search(message):
        match = re.search(r'[0-9a-f-]{4,}', message, re.IGNORECASE)
        if match:
            ok = delete_note(match.group(0))
            return {"response": "Note deleted." if ok else f"No note found with id '{match.group(0)}'.", "handled_by": "local-note", "complexity": 0.0}
        return {"response": "Which note? Say 'my notes' to see IDs, then 'delete note <id>'.", "handled_by": "local-note", "complexity": 0.0}

    return None


def _handle_photo_locally(message: str) -> dict | None:
    """Intercept photo listing/deletion requests. Returns result dict or None."""
    # List photos
    if _PHOTO_LIST_CHECK.search(message):
        from photos import list_photos, init_photos_db
        init_photos_db()
        photos = list_photos(limit=20)
        if not photos:
            return {"response": "No photos saved yet. Send a photo via Telegram to store one.", "handled_by": "local-photo", "complexity": 0.0}
        from zoneinfo import ZoneInfo
        from datetime import datetime, timezone
        tz = ZoneInfo(os.getenv("VESPERA_TIMEZONE", "America/Chicago"))
        lines = []
        for i, p in enumerate(photos, 1):
            try:
                dt = datetime.fromisoformat(p["created_at"]).astimezone(tz)
                date_str = dt.strftime("%b %d %I:%M %p")
            except Exception:
                date_str = ""
            caption = f" — {p['caption']}" if p.get("caption") else ""
            lines.append(f"{i}. [{p['id'][:8]}]{caption}  ({date_str})")
        return {"response": "\U0001f4f7 Your photos:\n" + "\n".join(lines), "handled_by": "local-photo", "complexity": 0.0}

    # Delete a photo
    if _PHOTO_DELETE_CHECK.search(message):
        match = re.search(r'[0-9a-f-]{4,}', message, re.IGNORECASE)
        if match:
            from photos import delete_photo, init_photos_db
            init_photos_db()
            ok = delete_photo(match.group(0))
            return {"response": "Photo deleted." if ok else f"No photo found with id '{match.group(0)}'.", "handled_by": "local-photo", "complexity": 0.0}
        return {"response": "Which photo? Say 'my photos' to see IDs, then 'delete photo <id>'.", "handled_by": "local-photo", "complexity": 0.0}

    return None


def _route_message(message: str, memories: str, recent: str) -> dict:
    """Core routing logic — returns a result dict without re-engagement suffix."""

    # Note requests — handle locally before complexity scoring
    note_result = _handle_note_locally(message)
    if note_result is not None:
        return note_result

    # Photo listing/deletion — handle locally before complexity scoring
    photo_result = _handle_photo_locally(message)
    if photo_result is not None:
        return photo_result

    # Reminder requests — handle locally before complexity scoring
    reminder_result = _handle_reminder_locally(message)
    if reminder_result is not None:
        return reminder_result

    complexity, reason, needs_search = score_complexity(message)
    log.info("Complexity: %.2f | search: %s — %s", complexity, needs_search, reason)



    if needs_search:
        results = web_search(message)
        if not results:
            return {"response": "I wasn't able to retrieve real-time information right now. Please try again in a moment.", "handled_by": "search-failed", "complexity": complexity}
        from datetime import datetime
        today = datetime.now().strftime("%A, %B %d, %Y")
        results_capped = results[:3000]
        if len(results) > 3000:
            results_capped += "\n[search results truncated]"
        formatted_prompt = SEARCH_RESPONSE_PROMPT.format(today=today, results=results_capped, message=message)
        if complexity >= COMPLEXITY_THRESHOLD and os.getenv("CLOUD_API_KEY", ""):
            response = respond_cloud(message, memories, recent, override_prompt=formatted_prompt)
            return {"response": _trim(response), "handled_by": "search+cloud", "complexity": complexity}
        response = call_local(formatted_prompt)
        if not response:
            response = "I found search results but couldn't summarize them — local model unavailable."
        return {"response": _trim(response), "handled_by": "search+local", "complexity": complexity}

    if complexity >= COMPLEXITY_THRESHOLD:
        if os.getenv("CLOUD_API_KEY", ""):
            response = respond_cloud(message, memories, recent)
            return {"response": _trim(response), "handled_by": "cloud", "complexity": complexity}
        response, _ = respond_locally(message, memories, recent)
        if not response:
            response = "I'm having trouble reaching my local model right now. Please check that Ollama is running."
        return {"response": _trim(response), "handled_by": "local", "complexity": complexity}

    response, needs_handoff = respond_locally(message, memories, recent)
    if needs_handoff:
        response = respond_cloud(message, memories, recent)
        return {"response": _trim(response), "handled_by": "cloud", "complexity": complexity}
    if not response:
        response = "I'm having trouble responding right now. Please check that Ollama is running."
    return {"response": _trim(response), "handled_by": "local", "complexity": complexity}


def handle_message(message: str) -> dict:
    message = _sanitize(message, 8000)

    # Check for re-engagement opportunity — surface a follow-up if returning after a gap
    reengagement_suffix = ""
    followup_id = None
    if _is_new_session():
        reengagement_suffix, followup_id = _get_reengagement_suffix()
        if reengagement_suffix:
            log.info("Re-engagement: surfacing follow-up %s", followup_id[:8] if followup_id else "none")

    memories, recent = get_context()
    result = _route_message(message, memories, recent)

    # Append follow-up question to response and mark it used
    if reengagement_suffix and followup_id and result.get("handled_by") != "search-failed":
        result["response"] = result["response"] + reengagement_suffix
        result["followup_asked"] = True
        try:
            mark_followup_used(followup_id)
        except Exception:
            log.exception("Failed to mark follow-up used")

    return result


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
