"""
Vespera Scheduler
-----------------
Set reminders and recurring tasks via chat.

Examples:
  "remind me to take my meds at 8pm"
  "remind me every morning at 7am to check emails"
  "cancel reminder 3"
  "list my reminders"

Storage: SQLite (same DB as memory, separate table)
Delivery: Telegram message + TTS voice (if configured)
No external calendar needed — fully local.
"""

import os
import re
import uuid
import threading
import concurrent.futures
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import sqlite3
from utils import get_logger, _sanitize
from handoff import call_local as _call_local

log = get_logger("scheduler")

DB_PATH  = Path(__file__).parent / "memory" / "vespera.db"
TIMEZONE = os.getenv("VESPERA_TIMEZONE", "America/Chicago")

_lock = threading.Lock()
_callbacks = []  # list of functions to call when reminder fires: fn(reminder)


# ─────────────────────────────────────────────
# DB SETUP
# ─────────────────────────────────────────────

@contextmanager
def _sched_connect():
    """Open scheduler DB connection, commit on success, rollback on error, always close."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_scheduler_db():
    """Initialize the database using the canonical schema.sql (single source of truth)."""
    from memory.store import init_db
    init_db()


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

def add_reminder(message: str, fire_at: datetime, recur: str = None, recur_rule: str = None) -> str:
    # recur_rule: reserved for future cron-style expressions; currently unused.
    rid = str(uuid.uuid4())
    # Always normalize to UTC so string comparisons in get_due_reminders() are correct
    if fire_at.tzinfo is not None:
        fire_at_utc = fire_at.astimezone(timezone.utc)
    else:
        fire_at_utc = fire_at.replace(tzinfo=timezone.utc)
    with _sched_connect() as conn:
        conn.execute(
            "INSERT INTO reminders (id, message, fire_at, recur, recur_rule, active, created_at) VALUES (?,?,?,?,?,1,?)",
            (rid, message, fire_at_utc.isoformat(), recur, recur_rule, datetime.now(timezone.utc).isoformat())
        )
    log.info("Reminder set: '%s' at %s (id: %s)", message, fire_at_utc.strftime('%Y-%m-%d %H:%M %Z'), rid)
    return rid


def list_reminders() -> list[dict]:
    with _sched_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE active=1 ORDER BY fire_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_reminder(rid: str) -> bool:
    with _sched_connect() as conn:
        cur = conn.execute("UPDATE reminders SET active=0 WHERE id=?", (rid,))
        affected = cur.rowcount
    return affected > 0


# Stale claim timeout — if a process crashes between claiming and firing,
# the claim is recovered after this many seconds so the reminder isn't lost.
_CLAIM_TIMEOUT_SECONDS = 300  # 5 minutes

def get_due_reminders() -> list[dict]:
    """Return due reminders, atomically claiming each so only one process fires it.
    Also recovers stale claims from crashed processes.
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    stale_cutoff = (now - relativedelta(seconds=_CLAIM_TIMEOUT_SECONDS)).isoformat()
    with _sched_connect() as conn:
        # Reset stale claims (process crashed between claim and fire)
        conn.execute(
            "UPDATE reminders SET claimed_at=NULL WHERE active=1 AND claimed_at IS NOT NULL AND claimed_at <= ?",
            (stale_cutoff,)
        )
        rows = conn.execute(
            "SELECT * FROM reminders WHERE active=1 AND fire_at <= ? AND claimed_at IS NULL",
            (now_iso,)
        ).fetchall()
        claimed = []
        for row in rows:
            cur = conn.execute(
                "UPDATE reminders SET claimed_at=? WHERE id=? AND claimed_at IS NULL",
                (now_iso, row["id"])
            )
            if cur.rowcount > 0:
                claimed.append(dict(row))
    return claimed


def reschedule_or_complete(reminder: dict):
    """After firing: reschedule if recurring, else deactivate."""
    recur = reminder.get("recur")
    if not recur:
        cancel_reminder(reminder["id"])
        return

    fire_at = datetime.fromisoformat(reminder["fire_at"])
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=timezone.utc)

    _DELTAS = {"daily": relativedelta(days=1), "weekly": relativedelta(weeks=1), "hourly": relativedelta(hours=1)}
    if recur not in _DELTAS:
        cancel_reminder(reminder["id"])
        return

    # Advance from the original anchor in fixed steps until past now.
    # This preserves time-of-day (a daily 8am reminder stays at 8am) while
    # still skipping missed intervals after downtime — never fires more than once.
    delta = _DELTAS[recur]
    now   = datetime.now(timezone.utc)
    next_fire = fire_at
    while next_fire <= now:
        next_fire += delta

    next_utc = next_fire.astimezone(timezone.utc)
    with _sched_connect() as conn:
        # Reset claimed_at so this reminder can be picked up again next cycle
        conn.execute(
            "UPDATE reminders SET fire_at=?, claimed_at=NULL WHERE id=?",
            (next_utc.isoformat(), reminder["id"])
        )
    log.info("Rescheduled '%s' → %s", reminder['message'], next_utc.strftime('%Y-%m-%d %H:%M %Z'))


# ─────────────────────────────────────────────
# PARSE NATURAL LANGUAGE
# ─────────────────────────────────────────────

# Recurrence keywords for direct detection — avoids relying on the local model for this
_RECUR_DAILY   = re.compile(r"\b(every\s+day|daily|each\s+day|every\s+morning|every\s+night|every\s+evening)\b", re.IGNORECASE)
_RECUR_WEEKLY  = re.compile(r"\b(every\s+week|weekly|each\s+week)\b", re.IGNORECASE)
_RECUR_HOURLY  = re.compile(r"\b(every\s+hour|hourly|each\s+hour)\b", re.IGNORECASE)

# Time-only patterns (no date) that dateutil reliably handles with a default
_TIME_ONLY_RE  = re.compile(
    r"\bat\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b"
    r"|\bin\s+\d+\s+(?:minute|hour)s?\b",
    re.IGNORECASE,
)


def _extract_recur(text: str) -> str | None:
    if _RECUR_HOURLY.search(text):  return "hourly"
    if _RECUR_DAILY.search(text):   return "daily"
    if _RECUR_WEEKLY.search(text):  return "weekly"
    return None


def _extract_message(text: str) -> str:
    """Extract the core reminder message from natural language input."""
    # 'remind me to X at Y' or 'remind me to X tomorrow' — grab the X part
    m = re.search(
        r"(?:remind\s+me\s+(?:every\s+\S+\s+)?(?:at\s+[\d:apm]+\s+)?to\s+)(.+?)(?:\s+(?:at|in|by|on|tomorrow|today|every|each)\s+.*)?$",
        text, re.IGNORECASE
    )
    if m:
        return m.group(1).strip()
    # fallback: strip leading boilerplate only
    cleaned = re.sub(
        r"^(?:remind\s+me\s+(?:to\s+)?|set\s+a\s+reminder\s+(?:to\s+)?|alert\s+me\s+(?:to\s+)?)",
        "", text, flags=re.IGNORECASE
    ).strip()
    return cleaned or text


def _parse_time_dateutil(text: str, tz: ZoneInfo, now: datetime) -> datetime | None:
    """Try to extract a fire_at datetime using dateutil — fast, no model needed."""
    # Handle 'in X minutes/hours' explicitly — dateutil doesn't do relative offsets
    rel = re.search(r"\bin\s+(\d+)\s+(minute|hour)s?\b", text, re.IGNORECASE)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2).lower()
        delta = relativedelta(minutes=n) if unit == "minute" else relativedelta(hours=n)
        return now + delta

    try:
        from dateutil import parser as du_parser
        # Use a default with zeroed minutes/seconds so 'at 9pm' gives 9:00:00 not 9:33:00
        default = now.replace(minute=0, second=0, microsecond=0)
        dt = du_parser.parse(text, default=default, fuzzy=True)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        # If parsed time is in the past and no explicit date was given, bump to tomorrow
        if dt <= now and _TIME_ONLY_RE.search(text):
            dt = dt + relativedelta(days=1)
        return dt if dt > now else None
    except Exception:
        return None


def parse_reminder(text: str) -> dict | None:
    """
    Parse a natural language reminder request.
    Strategy: dateutil first (fast, accurate for common formats);
    fall back to local model only when dateutil can't find a time.
    Returns dict with: message, fire_at (datetime), recur (str|None)
    """
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)
    safe_text = _sanitize(text, 500)

    # Detect recurrence directly — don't trust the model for this
    recur = _extract_recur(safe_text)

    # Try dateutil first
    fire_at = _parse_time_dateutil(safe_text, tz, now)

    # Fallback: local model
    if fire_at is None:
        now_str = now.strftime("%Y-%m-%d %H:%M %Z")
        prompt = f"""Parse this reminder request into structured data.

Current time: {now_str}

User request: "{safe_text}"

Return JSON only:
{{
  "message": "the reminder message (what to remind about, concise)",
  "fire_at": "ISO 8601 datetime with timezone offset, must be in the future",
  "recur": null
}}

Rules:
- fire_at must be in the future relative to current time above
- Use timezone: {TIMEZONE}
- If no date specified, assume today; if time already passed, use tomorrow
- Do NOT add recurrence — set recur to null always
- Keep message concise (strip 'remind me to' prefix)"""

        raw = _call_local(prompt)
        if not raw:
            log.error("Local model unavailable for reminder parsing")
            return None
        try:
            from utils import parse_json_response
            data = parse_json_response(raw)
            if not data or not data.get("fire_at"):
                log.error("Model returned no fire_at: %s", raw[:100])
                return None
            fire_at = datetime.fromisoformat(data["fire_at"])
            if fire_at.tzinfo is None:
                fire_at = fire_at.replace(tzinfo=tz)
            # Use model's message only if we don't have a better one
            model_message = _sanitize(str(data.get("message") or safe_text), 500)
        except Exception as e:
            log.error("Model parse error: %s | raw: %s", e, raw[:100])
            return None
    else:
        model_message = None  # will use _extract_message below

    # Validate fire_at
    if fire_at <= now:
        log.error("fire_at is in the past: %s", fire_at)
        return None
    max_future = now + relativedelta(years=1)
    if fire_at > max_future:
        log.error("fire_at is more than 1 year out: %s", fire_at)
        return None

    message = model_message or _extract_message(safe_text) or safe_text
    return {
        "message": _sanitize(message, 500),
        "fire_at": fire_at,
        "recur":   recur,
    }


# ─────────────────────────────────────────────
# FIRE REMINDER
# ─────────────────────────────────────────────

def fire_reminder(reminder: dict):
    log.info("🔔 REMINDER: %s", reminder['message'])

    # TTS
    try:
        from tts import speak
        audio = speak(f"Reminder: {reminder['message']}")
    except Exception:
        audio = None

    # Notify all registered callbacks in background threads so a slow
    # Telegram send never delays the next reminder from firing.
    # Snapshot the list first — prevents RuntimeError if register_callback()
    # appends concurrently while we're iterating.
    for cb in list(_callbacks):
        _callback_pool.submit(_safe_callback, cb, reminder, audio)

    # Reschedule or complete exactly once, regardless of callback count.
    try:
        reschedule_or_complete(reminder)
    except Exception as e:
        # On failure, deactivate rather than resetting claimed_at — resetting
        # claimed_at would cause the reminder to re-fire on the next tick,
        # potentially looping forever if the DB is in a bad state.
        log.error("reschedule_or_complete failed for %s — deactivating to prevent re-fire: %s", reminder["id"], e)
        try:
            with _sched_connect() as conn:
                conn.execute("UPDATE reminders SET active=0 WHERE id=?", (reminder["id"],))
        except Exception as deactivate_err:
            log.error("Could not deactivate reminder %s: %s", reminder["id"], deactivate_err)


def _safe_callback(fn, reminder: dict, audio):
    """Run a single reminder callback, swallowing exceptions so other callbacks still fire."""
    try:
        fn(reminder, audio)
    except Exception as e:
        log.error("Callback error: %s", e)


def register_callback(fn):
    """Register a function to call when a reminder fires. fn(reminder, audio_path)"""
    _callbacks.append(fn)


# ─────────────────────────────────────────────
# BACKGROUND LOOP
# ─────────────────────────────────────────────

_shutdown = threading.Event()  # fallback when run() is called without an event
# Bounded pool for reminder callbacks — prevents unbounded thread spawn under burst
_callback_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="reminder-cb")
import atexit as _atexit
_atexit.register(_callback_pool.shutdown, wait=False)

def run(shutdown_event: threading.Event = None):
    evt = shutdown_event if shutdown_event is not None else _shutdown
    init_scheduler_db()
    log.info("Started — checking every 30 seconds.")

    while not evt.is_set():
        try:
            due = get_due_reminders()
            for r in due:
                fire_reminder(r)
        except Exception as e:
            log.error("Error: %s", e)
        evt.wait(30)

    log.info("Stopped.")


if __name__ == "__main__":
    # Quick test
    init_scheduler_db()
    reminders = list_reminders()
    log.info("Active reminders: %d", len(reminders))
    for r in reminders:
        log.info("  [%s] %s — %s", r['id'], r['message'], r['fire_at'])
