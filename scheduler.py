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
import uuid
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

import sqlite3
from utils import get_logger
from handoff import call_local

log = get_logger("scheduler")

DB_PATH  = Path(__file__).parent / "memory" / "vespera.db"
TIMEZONE = os.getenv("VESPERA_TIMEZONE", "America/Chicago")

_lock = threading.Lock()
_callbacks = []  # list of functions to call when reminder fires: fn(reminder)


# ─────────────────────────────────────────────
# DB SETUP
# ─────────────────────────────────────────────

def _sched_connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_scheduler_db():
    """Initialize the database using the canonical schema.sql (single source of truth)."""
    from memory.store import init_db
    init_db()


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

def add_reminder(message: str, fire_at: datetime, recur: str = None, recur_rule: str = None) -> str:
    rid = uuid.uuid4().hex[:8]
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
        conn.commit()
    log.info("Reminder set: '%s' at %s (id: %s)", message, fire_at_utc.strftime('%Y-%m-%d %H:%M %Z'), rid)
    return rid


def list_reminders() -> list[dict]:
    with _sched_connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM reminders WHERE active=1 ORDER BY fire_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_reminder(rid: str) -> bool:
    with _sched_connect() as conn:
        cur = conn.execute("UPDATE reminders SET active=0 WHERE id=?", (rid,))
        conn.commit()
    return cur.rowcount > 0


def get_due_reminders() -> list[dict]:
    """Return due reminders, atomically claiming each so only one process fires it."""
    now = datetime.now(timezone.utc).isoformat()
    with _sched_connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM reminders WHERE active=1 AND fire_at <= ? AND claimed_at IS NULL",
            (now,)
        ).fetchall()
        claimed = []
        for row in rows:
            cur = conn.execute(
                "UPDATE reminders SET claimed_at=? WHERE id=? AND claimed_at IS NULL",
                (now, row["id"])
            )
            if cur.rowcount > 0:  # this process won the claim
                claimed.append(dict(row))
        conn.commit()
    return claimed


def reschedule_or_complete(reminder: dict):
    """After firing: reschedule if recurring, else deactivate."""
    from dateutil.relativedelta import relativedelta

    recur = reminder.get("recur")
    if not recur:
        cancel_reminder(reminder["id"])
        return

    fire_at = datetime.fromisoformat(reminder["fire_at"])
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=timezone.utc)
    if recur == "daily":
        next_fire = fire_at + relativedelta(days=1)
    elif recur == "weekly":
        next_fire = fire_at + relativedelta(weeks=1)
    elif recur == "hourly":
        next_fire = fire_at + relativedelta(hours=1)
    else:
        cancel_reminder(reminder["id"])
        return

    next_utc = next_fire.astimezone(timezone.utc)
    with _sched_connect() as conn:
        # Reset claimed_at so this reminder can be picked up again next cycle
        conn.execute(
            "UPDATE reminders SET fire_at=?, claimed_at=NULL WHERE id=?",
            (next_utc.isoformat(), reminder["id"])
        )
        conn.commit()
    log.info("Rescheduled '%s' → %s", reminder['message'], next_utc.strftime('%Y-%m-%d %H:%M %Z'))


# ─────────────────────────────────────────────
# PARSE NATURAL LANGUAGE
# ─────────────────────────────────────────────

def parse_reminder(text: str) -> dict | None:
    """
    Use the local model to parse a natural language reminder request.
    Returns dict with: message, fire_at (ISO), recur (daily/weekly/hourly/None)
    """
    tz = ZoneInfo(TIMEZONE)
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z")

    prompt = f"""Parse this reminder request into structured data.

Current time: {now_str}

User request: "{text}"

Return JSON only:
{{
  "message": "the reminder message",
  "fire_at": "ISO 8601 datetime with timezone offset",
  "recur": "daily" | "weekly" | "hourly" | null
}}

Rules:
- fire_at must be in the future
- Use timezone: {TIMEZONE}
- If no date specified, assume today
- "every morning" = daily at 7am
- "every day" = daily at same time
- Keep message concise"""

    raw = call_local(prompt)
    if not raw:
        return None

    try:
        from utils import parse_json_response
        data = parse_json_response(raw)
        if not data:
            return None
        fire_at = datetime.fromisoformat(data["fire_at"])
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=ZoneInfo(TIMEZONE))
        if fire_at <= datetime.now(tz=ZoneInfo(TIMEZONE)):
            log.error("Parsed fire_at is in the past: %s", fire_at)
            return None
        return {
            "message":  data.get("message", text),
            "fire_at":  fire_at,
            "recur":    data.get("recur"),
        }
    except Exception as e:
        log.error("Parse error: %s | raw: %s", e, raw[:100])
        return None


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

    # Notify all registered callbacks (Telegram, etc.)
    for cb in _callbacks:
        try:
            cb(reminder, audio)
        except Exception as e:
            log.error("Callback error: %s", e)

    reschedule_or_complete(reminder)


def register_callback(fn):
    """Register a function to call when a reminder fires. fn(reminder, audio_path)"""
    _callbacks.append(fn)


# ─────────────────────────────────────────────
# BACKGROUND LOOP
# ─────────────────────────────────────────────

_shutdown = threading.Event()

def run(shutdown_event: threading.Event = None):
    global _shutdown
    if shutdown_event:
        _shutdown = shutdown_event

    init_scheduler_db()
    log.info("Started — checking every 30 seconds.")

    while not _shutdown.is_set():
        try:
            due = get_due_reminders()
            for r in due:
                fire_reminder(r)
        except Exception as e:
            log.error("Error: %s", e)
        _shutdown.wait(30)

    log.info("Stopped.")


if __name__ == "__main__":
    # Quick test
    init_scheduler_db()
    reminders = list_reminders()
    log.info("Active reminders: %d", len(reminders))
    for r in reminders:
        log.info("  [%s] %s — %s", r['id'], r['message'], r['fire_at'])
