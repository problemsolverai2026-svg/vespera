"""
Vespera Cleanup Crew
--------------------
First-pass memory reviewer. Runs every 5 minutes.
Reviews 'recent' memories — promotes good ones to 'validated', prunes garbage.
"""

import re
import time
import threading
from config import get_component, CLEANUP_INTERVAL, CLEANUP_BATCH_SIZE
from memory.store import init_db, get_memories, promote_memory, prune_memory, store_fingerprint, get_fingerprints
from utils import get_logger, parse_json_response, _sanitize

log = get_logger("cleanup_crew")

_cfg         = get_component("cleanup_crew")
OLLAMA_URL   = _cfg["ollama_url"]
OLLAMA_MODEL = _cfg["ollama_model"]
BATCH_SIZE   = CLEANUP_BATCH_SIZE

CLEANUP_PROMPT = """You are reviewing a memory for a persistent AI memory system that stores facts about a user.

Memory:
{content}

DELETE if any of the following are true:
- Clearly incoherent, pure nonsense, or empty
- A direct duplicate of another memory
- Records the user's reaction, satisfaction, or emotion about a response (e.g. "Alfred was pleased", "the user seemed happy with", "Alfred appreciated", "Alfred liked the answer about")
- Describes how the AI responded or performed (e.g. "the assistant explained", "Rook provided", "the AI suggested")
- A general observation about the conversation rather than a fact about the user

KEEP if: any of the following are true:
- A personal fact about the user (name, preference, habit, goal, relationship, opinion, job, location)
- A project, decision, or commitment the user mentioned
- A useful reference, insight, or recurring topic
- Anything the user would reasonably expect to be remembered

When in doubt about a user fact, KEEP. But always DELETE satisfaction reactions and AI performance notes — those are never worth storing.

Respond in JSON only:
{{
  "decision": "keep" or "delete",
  "reason": "one short sentence"
}}"""


# ─────────────────────────────────────────────
# DEDUPLICATION
# ─────────────────────────────────────────────

_STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "is", "it", "i", "you", "we", "he", "she", "they", "do", "did",
    "have", "has", "had", "be", "been", "was", "were", "are", "that", "this",
    "not", "no", "so", "if", "my", "your", "me", "up", "any", "out", "as",
    "by", "from", "about", "what", "how", "when", "there", "just", "more",
    "also", "can", "will", "would", "could", "should", "get", "its", "than",
    "its", "user", "alfred", "thought", "recent", "note", "seem", "seems",
}
_DEDUP_THRESHOLD = 0.80  # word-overlap ratio above which a memory is a duplicate

# Action/transition words that vary between near-identical THOUGHT observations.
# Stripping these before comparing extracts the core topic — the actual fingerprint.
_THOUGHT_ACTION_WORDS = {
    "shift", "shifts", "shifted", "focus", "focused", "focusing", "focuses",
    "decision", "decided", "deciding", "discussing", "discussed", "discuss",
    "mention", "mentioned", "mentioning", "implement", "implementing", "implemented",
    "considering", "considered", "consider", "towards", "toward", "after", "following",
    "previous", "current", "currently", "recently", "now", "seems", "appears",
    "working", "talked", "talking", "started", "begin", "began", "moved",
    "transition", "switched", "pivot", "pivoting", "exploring", "looking",
    "trying", "adding", "added", "add", "take", "taking", "took", "need",
    "needs", "needed", "want", "wants", "wanted", "sitting", "suggest",
    "suggested", "suggesting", "indicate", "indicates", "indicated",
    "reflect", "reflects", "reflected", "show", "shows", "showed",
}
_FINGERPRINT_THRESHOLD = 0.65  # lower threshold used for THOUGHT fingerprint comparison


def _keywords(text: str) -> set:
    words = {w.lower() for w in re.findall(r'[a-zA-Z0-9]+', text) if len(w) > 2}
    return words - _STOP_WORDS


def _thought_fingerprint(content: str) -> set:
    """Extract core topic keywords from a THOUGHT memory, stripping action/transition
    words that vary across near-identical observations about the same subject."""
    text = re.sub(r'^THOUGHT:\s*', '', content, flags=re.IGNORECASE)
    text = re.sub(r"^Alfred'?s?\s+", '', text, flags=re.IGNORECASE)
    words = {w.lower() for w in re.findall(r'[a-zA-Z0-9]+', text) if len(w) > 2}
    return words - _STOP_WORDS - _THOUGHT_ACTION_WORDS


def _overlap_ratio(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _is_duplicate(content: str) -> bool:
    """Return True if a near-identical memory already exists in validated."""
    import sqlite3
    from pathlib import Path
    kws = _keywords(content)
    if len(kws) < 3:
        return False  # too short to reliably dedup
    # Pull validated candidates that share at least one keyword
    top_kws = sorted(kws, key=len, reverse=True)[:5]  # use top 5 longest keywords
    db_path = Path(__file__).parent / "memory" / "vespera.db"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        clauses = " OR ".join(["LOWER(content) LIKE ?" for _ in top_kws])
        params = [f"%{kw}%" for kw in top_kws]
        rows = conn.execute(
            f"SELECT content FROM memories WHERE layer='validated' AND pruned=0 AND ({clauses}) LIMIT 200",
            params
        ).fetchall()
        conn.close()
    except Exception:
        return False
    incoming = _keywords(content)
    is_thought = content.upper().startswith("THOUGHT:")
    incoming_fp = _thought_fingerprint(content) if is_thought else None

    for row in rows:
        existing = _keywords(row["content"])
        # Standard word-overlap check
        if _overlap_ratio(incoming, existing) >= _DEDUP_THRESHOLD:
            return True
        # Fingerprint check for THOUGHT memories — catches same topic in different phrasing
        if is_thought and incoming_fp and row["content"].upper().startswith("THOUGHT:"):
            existing_fp = _thought_fingerprint(row["content"])
            if len(incoming_fp) >= 3 and _overlap_ratio(incoming_fp, existing_fp) >= _FINGERPRINT_THRESHOLD:
                return True

    # Persistent fingerprint check — catches duplicates of PRUNED memories too.
    # This is the fix for fingerprints expiring when source memories are deleted.
    if is_thought and incoming_fp and len(incoming_fp) >= 3:
        stored_fps = get_fingerprints(limit=5000)
        for stored_fp in stored_fps:
            if stored_fp and _overlap_ratio(incoming_fp, stored_fp) >= _FINGERPRINT_THRESHOLD:
                return True

    return False


def call_local(prompt: str) -> str | None:
    from utils import call_ollama
    return call_ollama(OLLAMA_URL, OLLAMA_MODEL, prompt, temperature=0.1, timeout=30)


def review_memory(memory: dict) -> tuple[str, str]:
    raw = call_local(CLEANUP_PROMPT.format(content=_sanitize(memory["content"], 500)))
    if not raw:
        return "keep", "model unavailable"
    result = parse_json_response(raw)
    if not result or "decision" not in result:
        return "keep", "unparseable response"
    decision = result.get("decision")
    if not decision or not isinstance(decision, str):
        return "keep", "missing or null decision"
    return decision.lower(), _sanitize(result.get("reason", ""), 500)  # sanitize model output before storage


def run_cleanup():
    # Process oldest-reviewed first — consistent with periodic_pruning's anti-starvation fix.
    # Under a burst of background-loop output, newest entries would otherwise eclipse older ones.
    memories = get_memories(layer="recent", limit=BATCH_SIZE, order_by="created_at ASC")
    if not memories:
        log.debug("Nothing to review.")
        return
    log.info("Reviewing %d memories...", len(memories))
    kept = pruned = 0
    for memory in memories:
        decision, reason = review_memory(memory)
        if decision not in {"keep", "delete"}:
            log.warning("Unexpected decision '%s' for memory %s — treating as keep", decision, memory["id"][:8])
            decision = "keep"
        short_id = memory["id"][:8]
        if decision == "delete":
            prune_memory(memory["id"], reason=reason, pruned_by="cleanup_crew")
            log.info("PRUNED  %s — %s", short_id, reason)
            pruned += 1
        else:
            # Dedup check: don't promote if a near-identical memory already exists in validated.
            if _is_duplicate(memory["content"]):
                prune_memory(memory["id"], reason="duplicate of existing validated memory", pruned_by="cleanup_crew")
                log.info("DEDUP   %s — near-identical already in validated | %s...", short_id, memory["content"][:60])
                pruned += 1
            else:
                # Use 0.5 as a floor only — never downgrade a memory that already has a
                # higher trust score (e.g. one manually set to 0.8 by periodic_pruning).
                existing_score = memory.get("trust_score") or 0.0
                new_score = max(0.5, existing_score)
                promote_memory(memory["id"], new_trust_score=new_score)
                log.info("KEPT    %s → validated (trust=%.2f) | %s...", short_id, new_score, memory["content"][:60])
                kept += 1
    log.info("Done — kept: %d, pruned: %d", kept, pruned)


_shutdown = threading.Event()  # fallback used when run_loop() is called without an event

_DEDUP_FULL_EVERY = 5   # run retroactive dedup every N cleanup cycles (was 20)

def run_loop(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_db()
    log.info("Started — model: %s — every %ss", OLLAMA_MODEL, CLEANUP_INTERVAL)
    cycle = 0
    while not evt.is_set():
        try:
            run_cleanup()
            cycle += 1
            if cycle % _DEDUP_FULL_EVERY == 0:
                log.info("Running periodic full dedup scan (cycle %d)...", cycle)
                kept, pruned = run_dedup_validated()
                log.info("Full dedup done — kept: %d, pruned: %d", kept, pruned)
        except Exception as e:
            log.error("Error: %s", e)
        evt.wait(CLEANUP_INTERVAL)
    log.info("Stopped.")


def run_dedup_validated(dry_run: bool = False) -> tuple[int, int]:
    """One-shot retroactive dedup pass over all validated memories.
    Scans in creation order; prunes a memory if a near-identical one already
    appeared earlier in the scan (i.e. was created earlier and is the 'original').
    Returns (kept, pruned) counts.
    """
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).parent / "memory" / "vespera.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    rows = conn.execute(
        "SELECT id, content FROM memories WHERE layer='validated' AND pruned=0 ORDER BY created_at ASC"
    ).fetchall()
    conn.close()

    log.info("Dedup pass: %d validated memories to scan", len(rows))
    seen: list[set] = []       # keyword sets of kept memories
    seen_fp: list[tuple] = []   # (is_thought: bool, fingerprint: set) for kept memories
    kept = pruned = 0

    for row in rows:
        kws = _keywords(row["content"])
        is_thought = row["content"].upper().startswith("THOUGHT:")
        fp = _thought_fingerprint(row["content"]) if is_thought else set()
        if len(kws) < 3:
            kept += 1
            seen.append(kws)
            seen_fp.append((is_thought, fp))
            continue
        duplicate = any(_overlap_ratio(kws, s) >= _DEDUP_THRESHOLD for s in seen)
        if not duplicate and is_thought and len(fp) >= 3:
            duplicate = any(
                s_is_thought and _overlap_ratio(fp, s_fp) >= _FINGERPRINT_THRESHOLD
                for s_is_thought, s_fp in seen_fp
            )
        if duplicate:
            if not dry_run:
                prune_memory(row["id"], reason="retroactive dedup", pruned_by="cleanup_crew")
            content_preview = (row["content"] or "")[:80]
            log.info("DEDUP%s %s | %s...", " (dry)" if dry_run else "", (row["id"] or "")[:8], content_preview)
            pruned += 1
        else:
            seen.append(kws)
            seen_fp.append((is_thought, fp))
            kept += 1

    log.info("Dedup pass done — kept: %d, pruned: %d%s", kept, pruned, " (dry run)" if dry_run else "")
    return kept, pruned


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        init_db()
        run_cleanup()
    elif "--dedup" in sys.argv:
        init_db()
        dry = "--dry-run" in sys.argv
        kept, pruned = run_dedup_validated(dry_run=dry)
        print(f"Dedup complete — kept: {kept}, pruned: {pruned}{'  (dry run — nothing deleted)' if dry else ''}")
    else:
        run_loop()
