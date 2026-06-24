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
from notes import list_notes, init_notes_db
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

User's saved notes:
{notes}

A sample of your existing memories (do not repeat these):
{memories}

Things Alfred has already told you about related topics (build on these, don't re-ask):
{relevant_answers}

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


def _relevant_answers(convs: list) -> list[dict]:
    """Find stored answer_extraction memories that are topically relevant
    to the current conversation. Returns up to 5 matches."""
    if not convs:
        return []
    # Pull keywords from the last 4 conversation turns
    recent_text = " ".join(
        (c.get("content") or "") for c in convs[:4]
    ).lower()
    _STOP = {"the","a","an","is","are","was","were","and","or","but","in","on",
             "at","to","of","for","with","that","this","it","its","you","your",
             "i","me","my","we","he","she","they","have","has","had","do","did",
             "not","be","been","from","by","as","so","if","then","what","how",
             "when","where","who","would","could","should","will","can","just",
             "about","up","out","no","yes","okay","ok","yeah"}
    keywords = [
        w for w in re.findall(r'[a-zA-Z]{4,}', recent_text)
        if w not in _STOP
    ]
    # Deduplicate and take top 8 longest (most distinctive) keywords
    keywords = list(dict.fromkeys(keywords))
    keywords = sorted(keywords, key=len, reverse=True)[:8]
    if not keywords:
        return []
    # Search answer_extraction memories for keyword matches
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).parent / "memory" / "vespera.db"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        clauses = " OR ".join(["LOWER(content) LIKE ?" for _ in keywords])
        params = [f"%{kw}%" for kw in keywords]
        rows = conn.execute(
            f"SELECT * FROM memories WHERE pruned=0 AND source='answer_extraction' AND ({clauses}) ORDER BY trust_score DESC, created_at DESC LIMIT 5",
            params
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


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
    # Process thinking queue first — user-requested topics take priority over regular thinking
    queued = get_memories(layer="recent", limit=1, source_filter="think_queue")
    if queued:
        item = queued[0]
        topic = item["content"].replace("[THINK: ", "").rstrip("]")
        from memory.store import prune_memory
        prune_memory(item["id"], reason="think_queue item processed", pruned_by="background_loop")
        log.info("Processing think-queue item: %s", topic[:80])
        # Think-queue uses DuckDuckGo directly — free, no Venice cost
        from web_search import _search_duckduckgo
        raw_results = _search_duckduckgo(topic)
        result = " ".join(r.get("body", "") for r in raw_results)[:2000] if raw_results else None
        if result:
            thought = call_local(WEB_SEARCH_SUMMARY_PROMPT.format(question=topic, result=result[:2000]))
            if thought:
                return {"type": "thought", "content": f"[think-queue] {thought}"[:MAX_THOUGHT_LENGTH]}
        return None

    convs        = get_recent_conversations(limit=12)
    conversation = "\n".join(
        f"{c['role'].upper()}: {_sanitize(c['content'], 300)}" for c in reversed(convs)
    ) if convs else "No recent conversation."

    # Pull answers Alfred gave to past follow-up questions that are relevant
    # to the current conversation — inject these first so the model can build on them
    relevant = _relevant_answers(convs)
    relevant_text = "\n".join(
        f"- [past answer] {_sanitize(m['content'], 200)}" for m in relevant
    ) if relevant else ""

    memories     = _sample_memories()

    all_notes = list_notes()
    notes_text = "\n".join(
        f"- [{n['created_at'][:10]}] {_sanitize(n['content'], 200)}" for n in all_notes
    ) if all_notes else "No notes saved yet."

    raw = call_local(BACKGROUND_PROMPT.format(
        conversation=conversation,
        notes=notes_text,
        memories=memories,
        relevant_answers=relevant_text if relevant_text else "None.",
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
        if not _search_allowed():
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

    # Regular thought — dedup check before returning
    content = _sanitize(raw, MAX_THOUGHT_LENGTH)
    if not content:
        return None
    from cleanup_crew import _is_duplicate
    if _is_duplicate(content):
        log.debug("Background thought skipped — near-duplicate of existing memory.")
        return None
    return {"type": "thought", "content": content}


_shutdown = threading.Event()

# Web search rate limit — background auto-searches only, max 2 per hour
_SEARCH_MAX_PER_HOUR = 2
_search_timestamps: list = []  # rolling window of recent search times

def _search_allowed() -> bool:
    """Return True if a background web search is permitted under the rate limit."""
    import time
    now = time.time()
    cutoff = now - 3600  # 1 hour window
    # Prune old entries
    _search_timestamps[:] = [t for t in _search_timestamps if t > cutoff]
    if len(_search_timestamps) >= _SEARCH_MAX_PER_HOUR:
        log.debug("Web search rate limit hit (%d/%d per hour) — skipping.", len(_search_timestamps), _SEARCH_MAX_PER_HOUR)
        return False
    _search_timestamps.append(now)
    return True


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


_CORE_FOLLOWUP_EVERY = 10   # every N cycles, generate a follow-up from a core memory
_STALE_CHECK_EVERY   = 50   # every N cycles, flag a potentially stale core memory


def _core_driven_followup() -> dict | None:
    """Pick a core memory that hasn't been engaged with recently and generate
    a follow-up question that builds on it or checks if it's still current."""
    core = get_memories(layer="core", limit=50, order_by="updated_at ASC")
    if not core:
        return None
    # Pick the least-recently-touched one
    candidate = core[0]
    content = candidate.get("content", "")
    prompt = f"""You are a thoughtful AI assistant reviewing a long-term memory about the user.

Memory:
{content[:600]}

Generate ONE natural follow-up question that either:
1. Checks if this information is still current (things change)
2. Digs deeper into something mentioned
3. Connects it to something the user might be working on now

Prefix with FOLLOWUP: — keep it conversational, 1 sentence, specific to the memory content.
If nothing genuine to ask, respond NOTHING_NEW."""
    raw = call_local(prompt)
    if not raw or "NOTHING_NEW" in raw:
        return None
    match = re.search(r'FOLLOWUP:\s*([^\n]+)', raw, re.IGNORECASE)
    if match:
        question = _sanitize(match.group(1).strip(), MAX_THOUGHT_LENGTH)
        if question and not _followup_is_duplicate(question):
            # Touch the core memory so it rotates to back of queue
            from memory.store import touch_memory
            touch_memory(candidate["id"])
            return {"type": "followup", "content": question}
    return None


def run_loop(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    log.info("Started — model: %s — every %ss — CPU limit: %s%%",
             OLLAMA_MODEL, RUN_INTERVAL, CPU_THROTTLE_PERCENT)
    cycle = 0
    while not evt.is_set():
        try:
            cpu = psutil.cpu_percent(interval=1)
            if cpu > CPU_THROTTLE_PERCENT:
                log.debug("CPU at %.0f%% — skipping", cpu)
            else:
                cycle += 1
                # Every 10 cycles: generate a follow-up from a core memory
                if cycle % _CORE_FOLLOWUP_EVERY == 0:
                    result = _core_driven_followup()
                    if result:
                        log.info("Core-driven follow-up generated.")
                        _store_result(result)
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
