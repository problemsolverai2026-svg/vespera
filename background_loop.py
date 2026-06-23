"""
Vespera Background Loop
-----------------------
Persistent thinking engine. Runs 24/7, lightly reviews past conversations,
generates brief thoughts and follow-up questions, saves to memory.

Thoughts go through the cleanup pipeline (recent layer).
Follow-up questions skip cleanup and go straight to validated.
"""

import os
import re
import time
import random
import threading
import psutil
from config import get_component, BACKGROUND_LOOP_INTERVAL, MAX_THOUGHT_LENGTH

CPU_THROTTLE_PERCENT = float(os.getenv("VESPERA_CPU_LIMIT", "80"))
from web_search import search as _web_search
from memory.store import init_db, add_memory, get_memories, get_recent_conversations, get_followups
from utils import get_logger, _sanitize

log = get_logger("background_loop")

_cfg         = get_component("background_loop")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
RUN_INTERVAL = BACKGROUND_LOOP_INTERVAL

# Random sample size per run — every memory gets attention over time,
# prompt stays well within the local model's context window.
_MEMORY_SAMPLE_SIZE = 40
_MEMORY_ENTRY_CHARS = 200

BACKGROUND_PROMPT = """You are a persistent AI that thinks quietly between conversations with a user. Your job is to process what happened and either form a genuine thought or prepare a follow-up question for next time.

Recent conversation:
{conversation}

A sample of your existing memories (do not repeat these):
{memories}

Generate ONE of the following:

1. A THOUGHT — a brief observation, connection, or reflection on the conversation (2-3 sentences max). Be specific, not generic.

2. A FOLLOW-UP — a question you are genuinely curious about based on something the user mentioned. Prefix it with FOLLOWUP: (e.g. "FOLLOWUP: Last time you mentioned wanting to retire — have you figured out a timeline yet?")

3. SEARCH: <question> — if you need to look something up to better understand what was discussed.

4. NOTHING_NEW — if there is genuinely nothing worth generating right now.

Rules:
- Reference specific things from the conversation when possible
- Do NOT repeat anything already in your existing memories
- Follow-ups should feel natural, like something a friend would ask
- Max {max_length} characters"""

WEB_SEARCH_SUMMARY_PROMPT = """Summarize in 1-2 sentences, technically focused.
Question: {question}
Result: {result}"""


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.3, num_predict=200)


def _sample_memories() -> str:
    """
    Always include all core memories, then fill remaining slots with a random
    sample of validated memories (excluding follow-ups and satisfaction reactions).
    Core memories are the highest-quality facts — they should always be visible.
    """
    _SATISFACTION_WORDS = {"pleased", "satisfied", "happy", "appreciated", "liked", "enjoyed",
                           "glad", "relief", "excited", "loved", "grateful", "impressed"}

    core = get_memories(layer="core", limit=500, order_by="created_at DESC")
    all_validated = get_memories(layer="validated", limit=500, order_by="created_at DESC")

    def _is_noise(m: dict) -> bool:
        text = (m.get("content") or "").lower()
        if m.get("source") == "followup":
            return True
        if any(w in text for w in _SATISFACTION_WORDS):
            return True
        return False

    validated = [m for m in all_validated if not _is_noise(m)]

    remaining = max(0, _MEMORY_SAMPLE_SIZE - len(core))
    val_sample = random.sample(validated, min(remaining, len(validated)))
    sample = core + val_sample

    if not sample:
        return "No memories yet."
    return "\n".join(f"- {_sanitize(m['content'], _MEMORY_ENTRY_CHARS)}" for m in sample)


def think() -> dict | None:
    """Returns dict with 'type' ('thought' or 'followup') and 'content', or None."""
    memories     = _sample_memories()
    convs        = get_recent_conversations(limit=12)
    conversation = "\n".join(
        f"{c['role'].upper()}: {_sanitize(c['content'], 300)}" for c in reversed(convs)
    ) if convs else "No recent conversation."

    raw = call_local(BACKGROUND_PROMPT.format(
        conversation=conversation,
        memories=memories,
        max_length=MAX_THOUGHT_LENGTH,
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

    # FOLLOWUP: — question to ask the user next session
    # [^\ n]+ stops at the first newline — prevents capturing prose the model
    # appended after the question when re.DOTALL was in effect.
    followup_match = re.search(r'FOLLOWUP:\s*([^\n]+)', raw, re.IGNORECASE)
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
    return {"type": "thought", "content": content} if content else None


_shutdown = threading.Event()


_MAX_PENDING_FOLLOWUPS = 5   # don't pile up more than this many unused questions
_FOLLOWUP_TOPIC_WORDS   = 6  # top N words to compare for topic overlap


def _followup_is_duplicate(new_content: str) -> bool:
    """Return True if an existing unused followup covers the same topic."""
    existing = get_followups(limit=20)
    if len(existing) >= _MAX_PENDING_FOLLOWUPS:
        log.debug("Followup skipped — already %d pending", len(existing))
        return True
    # Simple keyword overlap check: extract significant words and compare
    stopwords = {"the","a","an","is","are","was","were","you","your","do","did",
                 "have","has","had","it","its","that","this","to","of","in","or",
                 "and","any","some","how","what","when","where","with","for","on",
                 "be","been","by","from","as","at","up","if","so","but","not","no"}
    def keywords(text):
        words = re.findall(r"[a-z0-9]+", text.lower())
        return set(w for w in words if w not in stopwords and len(w) > 2)
    new_kw = keywords(new_content)
    if not new_kw:
        return False
    for ex in existing:
        ex_kw = keywords(ex.get("content", ""))
        overlap = len(new_kw & ex_kw)
        if overlap >= 3:  # 3+ shared keywords = same topic
            log.debug("Followup skipped — topic overlap (%d words) with existing %s",
                      overlap, ex["id"][:8])
            return True
    return False


def _store_result(result: dict) -> None:
    """Thoughts → recent (cleanup pipeline). Follow-ups → validated directly."""
    kind    = result["type"]
    content = result["content"]
    if kind == "followup":
        if _followup_is_duplicate(content):
            return
        mem_id = add_memory(content=content, layer="validated", source="followup", trust_score=0.65)
        log.info("Follow-up stored (%s): %s", mem_id[:8], content[:80])
    else:
        mem_id = add_memory(content=content, layer="recent", source="background_loop")
        log.info("Thought saved    (%s): %s...", mem_id[:8], content[:80])


def run_loop(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    log.info("Started — model: %s — every %ss — CPU limit: %s%%",
             OLLAMA_MODEL, RUN_INTERVAL, CPU_THROTTLE_PERCENT)
    while not evt.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > CPU_THROTTLE_PERCENT:
                log.debug("CPU at %.0f%% — skipping", cpu)
            else:
                result = think()
                if result:
                    _store_result(result)
        except Exception as e:
            log.error("Error in think(): %s", e)
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
