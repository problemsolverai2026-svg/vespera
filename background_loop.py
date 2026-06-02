"""
Vespera Background Loop
-----------------------
Persistent thinking engine. Runs 24/7, lightly reviews past conversations,
generates brief thoughts, saves to 'recent' memory layer.
Uses web search for technical gaps instead of calling the cloud model.
"""

import os
import re
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

BACKGROUND_PROMPT = """You are a persistent AI that thinks quietly between conversations with a user. Your job is to process what happened and either form a genuine thought or prepare a follow-up question for next time.

Recent conversations:
{conversation}

Your recent thoughts (do not repeat these):
{memories}

Generate ONE of the following:

1. A THOUGHT — a brief observation, connection, or reflection on the conversation (2-3 sentences max). Be specific, not generic.

2. A FOLLOW-UP — a question you're genuinely curious about based on something the user mentioned. Prefix it with FOLLOWUP: (e.g. "FOLLOWUP: Last time you mentioned wanting to retire — have you figured out a timeline yet?")

3. SEARCH: <question> — if you need to look something up to better understand what was discussed.

4. NOTHING_NEW — if there's genuinely nothing worth generating right now.

Rules:
- Reference specific things from the conversation when possible
- Do NOT repeat anything already in your recent thoughts
- Follow-ups should feel natural, like something a friend would ask
- Max {max_length} characters"""

WEB_SEARCH_SUMMARY_PROMPT = """Summarize in 1-2 sentences, technically focused.
Question: {question}
Result: {result}"""


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.3, num_predict=200)


def think() -> dict | None:
    """Returns dict with 'type' ('thought' or 'followup') and 'content', or None."""
    # Pull everything from memory — it's already distilled, context window doesn't matter here.
    # Exclude follow-ups so we don't loop on our own questions.
    all_mems = get_memories(layer="core", limit=1000) + \
               [m for m in get_memories(layer="validated", limit=1000) if m.get("source") != "followup"]
    memories = "\n".join([f"- {_sanitize(m['content'], 200)}" for m in all_mems]) if all_mems else "No memories yet."

    # Just the last 2 raw exchanges for immediate context.
    convs = get_recent_conversations(limit=2)
    conversation = "\n".join(
        [f"{c['role'].upper()}: {_sanitize(c['content'], 150)}" for c in reversed(convs)]
    ) if convs else "No recent conversation."

    raw = call_local(BACKGROUND_PROMPT.format(
        conversation=conversation, memories=memories, max_length=MAX_THOUGHT_LENGTH
    ))
    if not raw:
        return None

    raw = raw.strip()

    # SEARCH: — look something up
    search_match = re.search(r'\bSEARCH:\s*(.+)', raw)
    if search_match:
        question = _sanitize(search_match.group(1).strip(), 300)
        if not question:
            log.debug("Empty SEARCH: query — skipping.")
            return None
        log.info("Web search: %s", question[:80])
        result = _web_search(question)
        if result:
            thought = call_local(WEB_SEARCH_SUMMARY_PROMPT.format(question=question, result=result[:2000]))
            if thought:
                return {"type": "thought", "content": f"[web] {thought}"[:MAX_THOUGHT_LENGTH]}
        return None

    # FOLLOWUP: — a question to ask the user next session
    followup_match = re.search(r'FOLLOWUP:\s*(.+)', raw, re.IGNORECASE)
    if followup_match:
        question = _sanitize(followup_match.group(1).strip(), MAX_THOUGHT_LENGTH)
        if question:
            return {"type": "followup", "content": question}
        return None

    # NOTHING_NEW
    if "NOTHING_NEW" in raw:
        log.debug("Nothing new this pass.")
        return None

    # Regular thought
    content = _sanitize(raw, MAX_THOUGHT_LENGTH)
    if content:
        return {"type": "thought", "content": content}
    return None


_shutdown = threading.Event()  # fallback used when run_loop() is called without an event

def _store_result(result: dict) -> None:
    """Store a think() result. Thoughts go to recent (cleanup pipeline). Follow-ups go straight to validated."""
    kind    = result["type"]
    content = result["content"]
    if kind == "followup":
        # Follow-up questions skip cleanup — they are intentional, not stray thoughts.
        mem_id = add_memory(content=content, layer="validated", source="followup", trust_score=0.65)
        log.info("Follow-up stored (%s): %s", mem_id[:8], content[:80])
    else:
        mem_id = add_memory(content=content, layer="recent", source="background_loop")
        log.info("Thought saved (%s): %s...", mem_id[:8], content[:80])


def run_loop(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    log.info("Started — model: %s — every %ss — CPU limit: %s%%", OLLAMA_MODEL, RUN_INTERVAL, CPU_THROTTLE_PERCENT)
    while not evt.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > CPU_THROTTLE_PERCENT:
                log.debug("CPU at %.0f%% — skipping this pass (limit: %.0f%%)", cpu, CPU_THROTTLE_PERCENT)
            else:
                result = think()
                if result:
                    _store_result(result)
        except Exception as e:
            log.error("Error: %s", e)
        evt.wait(RUN_INTERVAL)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        result = think()
        if result:
            _store_result(result)
        else:
            log.info("No output generated.")
    else:
        run_loop()
