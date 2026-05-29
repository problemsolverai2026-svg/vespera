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
import json
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
from handoff import call_local

DB_PATH  = Path(__file__).parent / "memory" / "vespera.db"
TIMEZONE = os.getenv("VESPERA_TIMEZONE", "America/Chicago")

_lock = threading.Lock()
_callbacks = []  # list of functions to call when reminder fires: fn(reminder)


# ─────────────────────────────────────────────
# DB SETUP
# ─────────────────────────────────────────────

def init_scheduler_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id          TEXT PRIMARY KEY,
                message     TEXT NOT NULL,
                fire_at     TEXT NOT NULL,
                recur       TEXT,
                recur_rule  TEXT,
                active      INTEGER DEFAULT 1,
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

def add_reminder(message: str, fire_at: datetime, recur: str = None, recur_rule: str = None) -> str:
    rid = uuid.uuid4().hex[:8]
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO reminders (id, message, fire_at, recur, recur_rule, active, created_at) VALUES (?,?,?,?,?,1,?)",
            (rid, message, fire_at.isoformat(), recur, recur_rule, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
    print(f"[Scheduler] Reminder set: '{message}' at {fire_at.strftime('%Y-%m-%d %H:%M %Z')} (id: {rid})")
    return rid


def list_reminders() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM reminders WHERE active=1 ORDER BY fire_at ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_reminder(rid: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute("UPDATE reminders SET active=0 WHERE id=?", (rid,))
        conn.commit()
    return cur.rowcount > 0


def get_due_reminders() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM reminders WHERE active=1 AND fire_at <= ?", (now,)
        ).fetchall()
    return [dict(r) for r in rows]


def reschedule_or_complete(reminder: dict):
    """After firing: reschedule if recurring, else deactivate."""
    from dateutil.relativedelta import relativedelta

    recur = reminder.get("recur")
    if not recur:
        cancel_reminder(reminder["id"])
        return

    fire_at = datetime.fromisoformat(reminder["fire_at"])
    if recur == "daily":
        next_fire = fire_at + relativedelta(days=1)
    elif recur == "weekly":
        next_fire = fire_at + relativedelta(weeks=1)
    elif recur == "hourly":
        next_fire = fire_at + relativedelta(hours=1)
    else:
        cancel_reminder(reminder["id"])
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE reminders SET fire_at=? WHERE id=?", (next_fire.isoformat(), reminder["id"]))
        conn.commit()
    print(f"[Scheduler] Rescheduled '{reminder['message']}' → {next_fire.strftime('%Y-%m-%d %H:%M %Z')}")


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
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        data  = json.loads(raw[start:end])
        fire_at = datetime.fromisoformat(data["fire_at"])
        if fire_at.tzinfo is None:
            fire_at = fire_at.replace(tzinfo=ZoneInfo(TIMEZONE))
        return {
            "message":  data.get("message", text),
            "fire_at":  fire_at,
            "recur":    data.get("recur"),
        }
    except Exception as e:
        print(f"[Scheduler] Parse error: {e} | raw: {raw[:100]}")
        return None


# ─────────────────────────────────────────────
# FIRE REMINDER
# ─────────────────────────────────────────────

def fire_reminder(reminder: dict):
    print(f"[Scheduler] 🔔 REMINDER: {reminder['message']}")

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
            print(f"[Scheduler] Callback error: {e}")

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
    print("[Scheduler] Started — checking every 30 seconds.")

    while not _shutdown.is_set():
        try:
            due = get_due_reminders()
            for r in due:
                fire_reminder(r)
        except Exception as e:
            print(f"[Scheduler] Error: {e}")
        _shutdown.wait(30)

    print("[Scheduler] Stopped.")


if __name__ == "__main__":
    # Quick test
    init_scheduler_db()
    reminders = list_reminders()
    print(f"Active reminders: {len(reminders)}")
    for r in reminders:
        print(f"  [{r['id']}] {r['message']} — {r['fire_at']}")
