"""
Vespera Web Search
------------------
Layered search providers — works out of the box for everyone.

Priority order:
  1. Venice  — if VENICE_API_KEY is set
  2. Brave   — if BRAVE_API_KEY is set
  3. DuckDuckGo — always available, no key needed

Users with no API keys get DuckDuckGo automatically.
"""

import os
import re
import requests
from utils import get_logger

log = get_logger("web_search")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

VENICE_API_KEY    = os.getenv("VENICE_API_KEY", "")
VENICE_SEARCH_URL = "https://api.venice.ai/api/v1/augment/search"

BRAVE_API_KEY     = os.getenv("BRAVE_API_KEY", "")
BRAVE_SEARCH_URL  = "https://api.search.brave.com/res/v1/web/search"

MAX_RESULTS = 4


# ─────────────────────────────────────────────
# PROVIDERS
# ─────────────────────────────────────────────

def _search_duckduckgo(query: str) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=MAX_RESULTS))
        return [{"title": r.get("title",""), "snippet": r.get("body",""), "url": r.get("href","")} for r in results]
    except Exception as e:
        log.error("DuckDuckGo error: %s", e)
        return []


def _search_brave(query: str) -> list[dict]:
    try:
        resp = requests.get(
            BRAVE_SEARCH_URL,
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            params={"q": query, "count": MAX_RESULTS},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("web", {}).get("results", [])
        return [{"title": r.get("title",""), "snippet": r.get("description",""), "url": r.get("url","")} for r in results]
    except Exception as e:
        log.error("Brave error: %s", e)
        return []


def _search_venice(query: str) -> list[dict]:
    try:
        resp = requests.post(
            VENICE_SEARCH_URL,
            headers={"Authorization": f"Bearer {VENICE_API_KEY}", "Content-Type": "application/json"},
            json={"query": query, "num_results": MAX_RESULTS},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [{"title": r.get("title",""), "snippet": r.get("snippet",""), "url": r.get("url","")} for r in results]
    except Exception as e:
        log.error("Venice error: %s", e)
        return []


# ─────────────────────────────────────────────
# MAIN ENTRY — auto-selects provider
# ─────────────────────────────────────────────

# Phrases that look like prompt injection attempts.
# Use word-boundary regex to avoid false positives on substrings like "interact as" → "act as".
_INJECTION_RE = re.compile(
    r"\b(?:"
    r"ignore\s+(?:all\s+)?previous"
    r"|disregard\s+previous"
    r"|new\s+instructions"
    r"|system\s+prompt"
    r"|you\s+are\s+now"
    r"|act\s+as\b"
    r"|forget\s+everything"
    r"|override\b"
    r"|jailbreak"
    r")",
    re.IGNORECASE,
)


def _sanitize_result(text: str) -> str:
    """Strip content that looks like prompt injection from a search result."""
    if _INJECTION_RE.search(text):
        return "[result removed — possible prompt injection]"
    return text


def search(query: str) -> str:
    """
    Search the web and return a formatted string of results.
    Auto-selects the best available provider.
    Returns empty string if all providers fail.
    """
    providers = []
    if VENICE_API_KEY:
        providers.append(("Venice", _search_venice))
    if BRAVE_API_KEY:
        providers.append(("Brave", _search_brave))
    providers.append(("DuckDuckGo", _search_duckduckgo))

    provider = "none"
    results = []
    for provider, fn in providers:
        results = fn(query)
        if results:
            break
        log.warning("%s returned no results, trying next provider", provider)

    if not results:
        return ""

    log.info("%s — %d results for: %s", provider, len(results), query)
    lines = []
    for i, r in enumerate(results, 1):
        title   = _sanitize_result(r['title'][:200])
        snippet = _sanitize_result(r['snippet'][:500])
        lines.append(f"{i}. {title}")
        lines.append(f"   {snippet}")
        lines.append(f"   {r['url']}")
    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    print(search("latest news today"))
