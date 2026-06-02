"""
Vespera Fact Extractor
----------------------
Extracts durable facts from user messages and stores them directly to memory.

This runs as a fire-and-forget background thread after every user chat message.
It is SEPARATE from the background loop's thought pipeline — facts the user
states go straight to 'validated' memory without passing through cleanup/pruning.

Source tag: "conversation" — distinguishes user-stated facts from AI-generated thoughts.
Trust score: 0.7 — higher than background thoughts (0.5), lower than core (0.95).
"""

import threading
from utils import get_logger, _sanitize, call_ollama
from config import get_component

log = get_logger("facts")

_cfg = get_component("background_loop")  # reuse same local model
OLLAMA_URL  = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]

FACT_EXTRACT_PROMPT = """Extract any durable facts from this user message that are worth remembering long-term.

User message: {message}

Capture facts about the user: names, preferences, favorites, habits, goals, projects,
relationships, opinions, commitments, important dates, job, location, or life context.

Rules:
- Write ONE fact per line in third person (e.g. "User's favorite food is crawfish étouffée")
- Only extract what is explicitly stated — do NOT invent or infer
- If there are no durable facts to extract, write: NOTHING_NEW

Facts:"""


def _run_extraction(message: str) -> None:
    """Runs in a background thread. Extracts facts and stores them directly to validated memory."""
    try:
        from memory.store import add_memory

        prompt = FACT_EXTRACT_PROMPT.format(message=_sanitize(message, 600))
        raw = call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.1, num_predict=250)

        if not raw:
            log.debug("Fact extraction: no response from model.")
            return

        raw = raw.strip()
        if "NOTHING_NEW" in raw and len(raw) < 20:
            log.debug("Fact extraction: nothing new.")
            return

        # Split on newlines — store each fact as a separate memory entry
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        facts = []
        for line in lines:
            # Skip lines that are just the sentinel or look like prompt artifacts
            if "NOTHING_NEW" in line:
                continue
            if line.lower().startswith("facts:"):
                continue
            if len(line) < 10:  # too short to be a real fact
                continue
            facts.append(line)

        if not facts:
            log.debug("Fact extraction: no usable facts after filtering.")
            return

        # Cap at 6 facts per message to prevent model hallucination floods
        for fact in facts[:6]:
            fact = _sanitize(fact, 400)
            if fact:
                mem_id = add_memory(
                    content=fact,
                    layer="validated",   # skip recent → bypass thought cleanup pipeline
                    source="conversation",
                    trust_score=0.7,
                )
                log.info("Stored fact (%s): %s", mem_id[:8], fact[:80])

    except Exception:
        log.exception("Fact extraction failed — skipping.")


def extract_facts_async(message: str) -> None:
    """Fire-and-forget: starts fact extraction in a daemon thread.
    Returns immediately — never blocks the chat response."""
    t = threading.Thread(
        target=_run_extraction,
        args=(message,),
        daemon=True,
        name="FactExtractor",
    )
    t.start()
