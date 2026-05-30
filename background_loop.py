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

BACKGROUND_PROMPT = """You are a persistent AI memory system. Lightly review past conversations and generate one brief, focused thought.

Focus ONLY on technical concepts. Ignore emotions.

Past conversation:
{conversation}

Recent memories:
{memories}

Task:
1. Identify the core technical idea
2. If you don't understand something technical, say: SEARCH: <question>
3. Otherwise write ONE short thought (2-3 sentences max)

Rules:
- Do NOT repeat what was already said — add a new angle
- If nothing new to add, say: NOTHING_NEW
- Max {max_length} characters"""

WEB_SEARCH_SUMMARY_PROMPT = """Summarize in 1-2 sentences, technically focused.
Question: {question}
Result: {result}"""


def call_local(prompt: str) -> str | None:
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 200},
        }, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message", {}).get("content") or data.get("response", "")).strip()
    except Exception as e:
        log.error("Model error: %s", e)
        return None


def think() -> str | None:
    convs = get_recent_conversations(limit=20)
    conversation = "\n".join(
        [f"{c['role'].upper()}: {_sanitize(c['content'], 200)}" for c in reversed(convs[:4])]
    ) if convs else "No conversations yet."

    mems = get_memories(layer="validated", limit=5) or get_memories(layer="core", limit=5)
    memories = "\n".join([f"- {_sanitize(m['content'], 150)}" for m in mems]) if mems else "No memories yet."

    raw = call_local(BACKGROUND_PROMPT.format(
        conversation=conversation, memories=memories, max_length=MAX_THOUGHT_LENGTH
    ))
    if not raw:
        return None

    if raw.startswith("SEARCH:"):
        question = raw[7:].strip()
        if not question:
            log.debug("Empty SEARCH: query from model — skipping.")
            return None
        log.info("Web search: %s", question[:80])
        result = _web_search(question)
        if result:
            thought = call_local(WEB_SEARCH_SUMMARY_PROMPT.format(question=question, result=result[:500]))
            return f"[web search] {thought}" if thought else None
        return None

    if "NOTHING_NEW" in raw:
        log.debug("Nothing new this pass.")
        return None

    return raw[:MAX_THOUGHT_LENGTH]


_shutdown = threading.Event()

def run_loop(shutdown_event: threading.Event = None):
    global _shutdown
    if shutdown_event:
        _shutdown = shutdown_event
    init_db()
    log.info("Started — model: %s — every %ss — CPU limit: %s%%", OLLAMA_MODEL, RUN_INTERVAL, CPU_THROTTLE_PERCENT)
    while not _shutdown.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > CPU_THROTTLE_PERCENT:
                log.debug("CPU at %.0f%% — skipping this pass (limit: %.0f%%)", cpu, CPU_THROTTLE_PERCENT)
            else:
                thought = think()
                if thought:
                    mem_id = add_memory(content=thought, layer="recent", source="background_loop")
                    log.info("Saved (%s): %s...", mem_id[:8], thought[:80])
        except Exception as e:
            log.error("Error: %s", e)
        _shutdown.wait(RUN_INTERVAL)
    log.info("Stopped.")


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        thought = think()
        if thought:
            mem_id = add_memory(content=thought, layer="recent", source="background_loop")
            log.info("Saved (%s): %s", mem_id[:8], thought)
        else:
            log.info("No thought generated.")
    else:
        run_loop()
