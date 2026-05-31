"""
Vespera Utilities
-----------------
Shared helpers: logging setup and robust JSON parsing.
"""

import json
import logging
import os
import re
import requests as _requests


# ─────────────────────────────────────────────
# INJECTION SANITIZER (shared with background workers)
# ─────────────────────────────────────────────

_INJECTION_RE = re.compile(
    r"\b(?:"
    r"ignore\s+(?:all\s+)?previous"
    r"|disregard\s+previous"
    r"|new\s+instructions"
    r"|system\s+prompt"
    r"|you\s+are\s+now"
    r"|act\s+as\b"
    r"|forget\s+everything"
    r"|jailbreak"
    r")",
    re.IGNORECASE,
)


def _sanitize(text: str, max_len: int) -> str:
    """Truncate and strip potential injection attempts from memory/conversation content."""
    truncated = text[:max_len]
    if _INJECTION_RE.search(truncated):
        return "[content removed — possible injection attempt]"
    return truncated


def get_logger(name: str) -> logging.Logger:
    """
    Return a module-level logger with consistent format.
    Level controlled by VESPERA_LOG_LEVEL env var (default INFO).
    """
    level = os.getenv("VESPERA_LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level, logging.INFO))
    return logger


def parse_json_response(raw: str) -> dict | None:
    """
    Robustly extract a JSON object from a model response.

    Handles:
    - Plain JSON: {"key": "value"}
    - Markdown fences: ```json\\n{...}\\n```
    - JSON buried in prose: "Here is the result: {...}"
    - Nested braces (bracket-counter, not naive find/rfind)

    Returns None if no valid JSON object found.
    """
    if not raw:
        return None

    # 1. Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. Try the whole string as-is
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # 3. Find the outermost { ... } using a bracket counter
    start = raw.find("{")
    if start == -1:
        return None

    depth = 0
    end = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(raw[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        return None

    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None


# ─────────────────────────────────────────────
# SHARED OLLAMA CALLER
# ─────────────────────────────────────────────

_ollama_log = get_logger("ollama")


def call_ollama(
    url: str,
    model: str,
    prompt: str,
    temperature: float = 0.3,
    num_predict: int = None,
    timeout: int = 60,
) -> str | None:
    """Call a local Ollama model and return its text response, or None on error."""
    resp = None
    options: dict = {"temperature": temperature}
    if num_predict is not None:
        options["num_predict"] = num_predict
    try:
        resp = _requests.post(
            url,
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": options,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message", {}).get("content") or data.get("response", "")).strip()
    except Exception as e:
        _ollama_log.error("Model error (%s): %s", model, e)
        return None
    finally:
        try:
            if resp:
                resp.close()
        except Exception:
            pass
