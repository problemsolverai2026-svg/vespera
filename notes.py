"""
Vespera Notes
-------------
Simple persistent note-taking via Telegram or chat.

Usage:
  "note: pick up milk"           → saves a note
  "note to self: call dentist"   → saves a note
  "show my notes" / "my notes"   → lists all notes
  "delete note 3" / "remove note abc123" → deletes a note

Storage: SQLite (same DB as memory)
"""

import uuid
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from utils import get_logger, _sanitize

log = get_logger("notes")

DB_PATH = Path(__file__).parent / "memory" / "vespera.db"
_lock = threading.Lock()


@contextmanager
def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_notes_db():
    """Create the notes table if it doesn't exist."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         TEXT PRIMARY KEY,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)


def add_note(content: str) -> dict:
    content = _sanitize(content.strip(), 2000)
    if not content:
        raise ValueError("Note content cannot be empty")
    nid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO notes (id, content, created_at) VALUES (?, ?, ?)",
            (nid, content, now)
        )
    log.info("Note saved (%s): %s", nid[:8], content[:80])
    return {"id": nid, "content": content, "created_at": now}


def list_notes() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_note(note_id: str) -> bool:
    """Delete by full ID or ID prefix (min 4 chars)."""
    note_id = note_id.strip()
    with _connect() as conn:
        if len(note_id) >= 36:
            cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        else:
            cur = conn.execute("DELETE FROM notes WHERE id LIKE ?", (note_id + "%",))
        return cur.rowcount > 0


def get_note(note_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM notes WHERE id = ? OR id LIKE ?",
            (note_id, note_id + "%")
        ).fetchone()
    return dict(row) if row else None
