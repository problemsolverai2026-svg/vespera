"""
Vespera Photos
--------------
Store and retrieve photos sent via Telegram.

Usage (Telegram):
  Send any photo (with optional caption) → saved to disk + DB
  "show my photos" / "my photos"         → lists recent photos
  "delete photo <id>"                     → deletes a photo by ID prefix

Storage: SQLite (same DB as memory) + ~/.vespera/photos/ directory
AI description: reserved column — stub only; not called until explicitly enabled.
"""

import uuid
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from utils import get_logger, _sanitize

log = get_logger("photos")

DB_PATH = Path(__file__).parent / "memory" / "vespera.db"
PHOTOS_DIR = Path.home() / ".vespera" / "photos"
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


def init_photos_db():
    """Create the photos table and storage directory if they don't exist."""
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id          TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                caption     TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL
            )
        """)


def add_photo(filename: str, caption: str = "") -> dict:
    """Save a photo record. File must already exist in PHOTOS_DIR."""
    caption = _sanitize(caption.strip(), 500)
    pid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO photos (id, filename, caption, description, created_at) VALUES (?, ?, ?, ?, ?)",
            (pid, filename, caption, "", now),
        )
    log.info("Photo saved (%s): %s — %s", pid[:8], filename, caption[:60] or "(no caption)")
    return {"id": pid, "filename": filename, "caption": caption, "description": "", "created_at": now}


def list_photos(limit: int = 20) -> list:
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM photos ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_photo(photo_id: str) -> dict | None:
    photo_id = photo_id.strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM photos WHERE id = ? OR id LIKE ?",
            (photo_id, photo_id + "%"),
        ).fetchone()
    return dict(row) if row else None


def delete_photo(photo_id: str) -> bool:
    """Delete DB record and file. Accepts full UUID or prefix (min 4 chars)."""
    photo_id = photo_id.strip()
    with _lock:
        with _connect() as conn:
            if len(photo_id) >= 36:
                row = conn.execute("SELECT filename FROM photos WHERE id = ?", (photo_id,)).fetchone()
            else:
                row = conn.execute("SELECT filename FROM photos WHERE id LIKE ?", (photo_id + "%",)).fetchone()
            if not row:
                return False
            filename = row["filename"]
            if len(photo_id) >= 36:
                cur = conn.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            else:
                cur = conn.execute("DELETE FROM photos WHERE id LIKE ?", (photo_id + "%",))
            deleted = cur.rowcount > 0
        if deleted:
            try:
                (PHOTOS_DIR / filename).unlink(missing_ok=True)
                log.info("Photo file deleted: %s", filename)
            except Exception as e:
                log.warning("Could not delete photo file %s: %s", filename, e)
        return deleted


def search_photos(query: str, limit: int = 20) -> list:
    """Search photos by caption keyword (case-insensitive)."""
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM photos WHERE LOWER(caption) LIKE LOWER(?) ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def photo_path(filename: str) -> Path:
    """Return the absolute path to a stored photo file."""
    return PHOTOS_DIR / filename


# ─────────────────────────────────────────────
# VIDEO STORAGE
# ─────────────────────────────────────────────

VIDEOS_DIR = Path.home() / ".vespera" / "videos"


def init_videos_db():
    """Create the videos table and storage directory if they don't exist."""
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS videos (
                id          TEXT PRIMARY KEY,
                filename    TEXT NOT NULL,
                caption     TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                duration_s  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)


def add_video(filename: str, caption: str = "", duration_s: int = 0) -> dict:
    """Save a video record. File must already exist in VIDEOS_DIR."""
    caption = _sanitize(caption.strip(), 500)
    vid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO videos (id, filename, caption, description, duration_s, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (vid, filename, caption, "", max(0, int(duration_s)), now),
        )
    log.info("Video saved (%s): %s — %s", vid[:8], filename, caption[:60] or "(no caption)")
    return {"id": vid, "filename": filename, "caption": caption, "duration_s": duration_s, "created_at": now}


def list_videos(limit: int = 20) -> list:
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM videos ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_video(video_id: str) -> dict | None:
    video_id = video_id.strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM videos WHERE id = ? OR id LIKE ?",
            (video_id, video_id + "%"),
        ).fetchone()
    return dict(row) if row else None


def delete_video(video_id: str) -> bool:
    """Delete DB record and file. Accepts full UUID or prefix (min 4 chars)."""
    video_id = video_id.strip()
    with _lock:
        with _connect() as conn:
            if len(video_id) >= 36:
                row = conn.execute("SELECT filename FROM videos WHERE id = ?", (video_id,)).fetchone()
            else:
                row = conn.execute("SELECT filename FROM videos WHERE id LIKE ?", (video_id + "%",)).fetchone()
            if not row:
                return False
            filename = row["filename"]
            if len(video_id) >= 36:
                cur = conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
            else:
                cur = conn.execute("DELETE FROM videos WHERE id LIKE ?", (video_id + "%",))
            deleted = cur.rowcount > 0
        if deleted:
            try:
                (VIDEOS_DIR / filename).unlink(missing_ok=True)
                log.info("Video file deleted: %s", filename)
            except Exception as e:
                log.warning("Could not delete video file %s: %s", filename, e)
        return deleted


def search_videos(query: str, limit: int = 20) -> list:
    """Search videos by caption keyword (case-insensitive)."""
    query = query.strip()
    if not query:
        return []
    limit = max(1, min(int(limit), 500))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM videos WHERE LOWER(caption) LIKE LOWER(?) ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    return [dict(r) for r in rows]


def video_path(filename: str) -> Path:
    """Return the absolute path to a stored video file."""
    return VIDEOS_DIR / filename
