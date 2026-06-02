"""
Vespera Background Loop
-----------------------
Persistent thinking engine. Runs 24/7, lightly reviews past conversations,
generates brief thoughts, saves to 'recent' memory layer.
Uses web search for technical gaps instead of calling the cloud model.
"""

import os
import time
import threading
import psutil
import requests
from config import get_component, BACKGROUND_LOOP_INTERVAL, MAX_THOUGHT_LENGTH

CPU_THROTTLE_PERCENT = float(os.getenv("VESPERA_CPU_LIMIT", "80"))  # skip run if CPU above this %
from web_search import search as _web_search
from memory.store import init_db, add_memory, get_memories, get_recent_conversations
from utils import get_logger, _sanitize

log = get_logger("background_loop")

_cfg             = get_component("background_loop")
OLLAMA_URL       = _cfg["ollama_url"]
OLLAMA_MODEL     = _cfg["ollama_model"]
RUN_INTERVAL     = BACKGROUND_LOOP_INTERVAL

BACKGROUND_PROMPT = """You are a persistent memory system for a single user. Review the recent conversation and extract any facts worth remembering long-term.

Past conversation:
{conversation}

Already stored memories (do not repeat these):
{memories}

Capture ANYTHING durable about the user, including:
- Names, nicknames, and relationships (family, friends, coworkers)
- Projects, goals, and decisions
- Preferences and favorites (food, music, hobbies, shows, sports teams, anything)
- Habits, routines, and things they do often
- Opinions and values they express
- Important dates and commitments
- Things they mention repeatedly across conversations

Write each as one concise, self-contained statement in third person (e.g. "User's favorite band is Metallica" or "User drinks black coffee every morning").

Rules:
- Do NOT invent details not present in the conversation
- Do NOT repeat facts already in stored memories
- If you need to look something up, say: SEARCH: <question>
- If the conversation contains no new facts worth storing, say: NOTHING_NEW
- Max {max_length} characters total"""

WEB_SEARCH_SUMMARY_PROMPT = """Summarize in 1-2 sentences, technically focused.
Question: {question}
Result: {result}"""


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.3, num_predict=200)


def think() -> str | None:
    convs = get_recent_conversations(limit=4)  # only use 4 — fetching 20 and slicing was wasteful
    conversation = "\n".join(
        [f"{c['role'].upper()}: {_sanitize(c['content'], 200)}" for c in reversed(convs)]
    ) if convs else "No conversations yet."

    mems = get_memories(layer="validated", limit=5) or get_memories(layer="core", limit=5)
    memories = "\n".join([f"- {_sanitize(m['content'], 150)}" for m in mems]) if mems else "No memories yet."

    raw = call_local(BACKGROUND_PROMPT.format(
        conversation=conversation, memories=memories, max_length=MAX_THOUGHT_LENGTH
    ))
    if not raw:
        return None

    if raw.startswith("SEARCH:"):
        question = _sanitize(raw[7:].strip(), 300)
        if not question:
            log.debug("Empty SEARCH: query from model — skipping.")
            return None
        log.info("Web search: %s", question[:80])
        result = _web_search(question)
        if result:
            thought = call_local(WEB_SEARCH_SUMMARY_PROMPT.format(question=question, result=result[:2000]))
            return f"[web search] {thought}"[:MAX_THOUGHT_LENGTH] if thought else None
        return None

    if "NOTHING_NEW" in raw:
        log.debug("Nothing new this pass.")
        return None

    return raw[:MAX_THOUGHT_LENGTH]


_shutdown = threading.Event()  # fallback used when run_loop() is called without an event

def run_loop(shutdown_event: threading.Event = None):
    # Use a local reference rather than reassigning the module-level variable —
    # avoids the global-reassignment anti-pattern that could confuse concurrent callers.
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    log.info("Started — model: %s — every %ss — CPU limit: %s%%", OLLAMA_MODEL, RUN_INTERVAL, CPU_THROTTLE_PERCENT)
    while not evt.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > CPU_THROTTLE_PERCENT:
                log.debug("CPU at %.0f%% — skipping this pass (limit: %.0f%%)", cpu, CPU_THROTTLE_PERCENT)
            else:
                thought = think()
                if thought:
                    thought = _sanitize(thought, 500)  # sanitize model output before storage
                    mem_id = add_memory(content=thought, layer="recent", source="background_loop")
                    log.info("Saved (%s): %s...", mem_id[:8], thought[:80])
        except Exception as e:
            log.error("Error: %s", e)
        evt.wait(RUN_INTERVAL)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        thought = think()
        if thought:
            thought = _sanitize(thought, 500)  # sanitize model output before storage
            mem_id = add_memory(content=thought, layer="recent", source="background_loop")
            log.info("Saved (%s): %s", mem_id[:8], thought)
        else:
            log.info("No thought generated.")
    else:
        run_loop()
