"""
Vespera Memory Store
--------------------
The core memory layer for the Vespera persistent AI system.

Nesting doll structure (outer → inner):
  working   → ephemeral, active conversation context
  recent    → fresh background loop thoughts, unreviewed
  validated → cleanup crew approved, awaiting core promotion
  core      → Trusted AI approved (95% confidence), permanent

Memories can be linked to each other — related, expands, contradicts, references.
"""

import sqlite3
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from utils import get_logger

log = get_logger("memory.store")


DB_PATH = Path(__file__).parent / "vespera.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

LAYERS = ["working", "recent", "validated", "core"]
LAYER_ORDER = {layer: i for i, layer in enumerate(LAYERS)}

_init_lock = threading.Lock()  # prevents concurrent init_db() calls on first boot


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    """Open a SQLite connection, yield it, commit on success, rollback on error,
    and always close — preventing file descriptor leaks under long-running threads."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # allows concurrent readers + one writer
    conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s instead of failing instantly
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if `column` already exists in `table`."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_db():
    """Initialize the database from schema. Thread-safe — safe to call concurrently."""
    with _init_lock:
        schema = SCHEMA_PATH.read_text()
        with _connect() as conn:
            conn.executescript(schema)
        # Run migrations inside the lock — two LaunchAgents starting simultaneously
        # both see the column missing and both fire ALTER TABLE; the second crashes.
        # executescript() issues an implicit COMMIT so schema + migration still need
        # separate connections, but the lock prevents the race.
        with _connect() as conn:
            for col, typedef in [("used_cloud", "INTEGER DEFAULT 0"), ("complexity", "REAL DEFAULT 0.0")]:
                if not _column_exists(conn, "conversations", col):
                    conn.execute(f"ALTER TABLE conversations ADD COLUMN {col} {typedef}")
                    log.info("Migrated conversations: added column %s", col)
    log.info("Memory store initialized at %s", DB_PATH)


# ─────────────────────────────────────────────
# WRITE
# ─────────────────────────────────────────────

def add_memory(
    content: str,
    layer: str = "recent",
    source: str = "background_loop",
    trust_score: float = 0.0,
    tags: list[str] = None,
) -> str:
    """Add a new memory. Returns its ID."""
    if layer not in LAYERS:
        raise ValueError(f"Invalid layer: {layer}. Must be one of {LAYERS}")

    memory_id = str(uuid.uuid4())
    now = _now()

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO memories (id, content, layer, source, trust_score, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, content, layer, source, trust_score, json.dumps(tags or []), now, now),
        )

    return memory_id


def add_conversation(role: str, content: str, summary: str = None,
                     used_cloud: bool = False, complexity: float = 0.0) -> str:
    """Log a conversation message. Returns its ID."""
    conv_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, timestamp, role, content, summary, used_cloud, complexity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (conv_id, _now(), role, content, summary, 1 if used_cloud else 0, complexity),
        )
    return conv_id


def link_memories(
    memory_id_a: str,
    memory_id_b: str,
    relationship: str = "related",
    strength: float = 0.5,
) -> str:
    """Create a link between two memories. Returns link ID."""
    valid_relationships = ["related", "expands", "contradicts", "references"]
    if relationship not in valid_relationships:
        raise ValueError(f"Invalid relationship: {relationship}")

    link_id = str(uuid.uuid4())
    with _connect() as conn:
        # Verify both memories exist before inserting — bad IDs cause opaque IntegrityError
        missing = []
        for mid in (memory_id_a, memory_id_b):
            row = conn.execute("SELECT id FROM memories WHERE id = ? AND pruned = 0", (mid,)).fetchone()
            if not row:
                missing.append(mid)
        if missing:
            raise ValueError(f"Cannot link: memory ID(s) not found or pruned: {missing}")
        conn.execute(
            """
            INSERT INTO memory_links (id, memory_id_a, memory_id_b, relationship, strength, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (link_id, memory_id_a, memory_id_b, relationship, strength, _now()),
        )
    return link_id


# ─────────────────────────────────────────────
# PROMOTE / DEMOTE
# ─────────────────────────────────────────────

def promote_memory(memory_id: str, new_trust_score: float = None) -> bool:
    """Move a memory one layer inward (e.g. recent → validated)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT layer FROM memories WHERE id = ? AND pruned = 0", (memory_id,)
        ).fetchone()

        if not row:
            return False

        current = row["layer"]
        current_idx = LAYER_ORDER[current]

        if current_idx >= len(LAYERS) - 1:
            log.debug("Memory %s is already at core layer.", memory_id)
            return False

        next_layer = LAYERS[current_idx + 1]
        # Atomic claim: include current layer in WHERE so a concurrent caller
        # that already promoted this memory gets rowcount=0 and exits cleanly.
        cur = conn.execute(
            "UPDATE memories SET layer = ?, trust_score = COALESCE(?, trust_score), updated_at = ? WHERE id = ? AND layer = ?",
            (next_layer, new_trust_score, _now(), memory_id, current),
        )
        if cur.rowcount == 0:
            log.debug("Memory %s already promoted by concurrent caller.", memory_id[:8])
            return False
        log.info("Promoted %s: %s → %s", memory_id[:8], current, next_layer)
        return True


def touch_memory(memory_id: str):
    """Bump updated_at on a memory so it sorts to the back of 'oldest reviewed first' queries.
    Used by periodic_pruning after a 'keep' decision to prevent starvation of older records.
    """
    with _connect() as conn:
        conn.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (_now(), memory_id))


def prune_memory(memory_id: str, reason: str, pruned_by: str = "cleanup_crew"):
    """Soft-delete a memory and log it."""
    with _connect() as conn:
        # Atomic claim: UPDATE wins the race; second caller gets rowcount=0 and exits early
        cur = conn.execute(
            "UPDATE memories SET pruned = 1, updated_at = ? WHERE id = ? AND pruned = 0",
            (_now(), memory_id),
        )
        if cur.rowcount == 0:
            return  # Not found or already claimed by a concurrent caller

        row = conn.execute(
            "SELECT content FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()

        conn.execute(
            """
            INSERT INTO prune_log (id, memory_id, reason, pruned_by, pruned_at, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), memory_id, reason, pruned_by, _now(),
             row["content"] if row else ""),
        )
        log.info("Pruned %s (%s): %s", memory_id[:8], pruned_by, reason)


# ─────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────

def get_memories(
    layer: str = None,
    limit: int = 20,
    include_pruned: bool = False,
    order_by: str = "created_at DESC",
) -> list[dict]:
    """Fetch memories, optionally filtered by layer.

    order_by: SQL ORDER BY clause. Periodic pruning uses 'updated_at ASC' to
    process oldest-reviewed memories first and avoid starvation of older records.
    """
    if layer is not None and layer not in LAYERS:
        raise ValueError(f"Invalid layer: {layer!r}. Must be one of {LAYERS}")
    # Whitelist order_by to prevent SQL injection via this parameter
    _VALID_ORDER = {"created_at DESC", "created_at ASC", "updated_at DESC", "updated_at ASC", "trust_score DESC", "trust_score ASC"}
    if order_by not in _VALID_ORDER:
        raise ValueError(f"Invalid order_by: {order_by!r}")
    query = "SELECT * FROM memories WHERE 1=1"
    params = []

    if not include_pruned:
        query += " AND pruned = 0"
    if layer:
        query += " AND layer = ?"
        params.append(layer)

    query += f" ORDER BY {order_by} LIMIT ?"
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()

    return [dict(r) for r in rows]


def get_linked_memories(memory_id: str) -> list[dict]:
    """Get all memories linked to a given memory."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT m.*, ml.relationship, ml.strength
            FROM memories m
            JOIN memory_links ml ON (ml.memory_id_a = ? AND ml.memory_id_b = m.id)
                                 OR (ml.memory_id_b = ? AND ml.memory_id_a = m.id)
            WHERE m.pruned = 0
            ORDER BY ml.strength DESC
            """,
            (memory_id, memory_id),
        ).fetchall()

    return [dict(r) for r in rows]


def get_recent_conversations(limit: int = 20) -> list[dict]:
    """Get the most recent conversation messages."""
    limit = max(1, min(int(limit), 1000))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, timestamp, role, content, summary, used_cloud, complexity FROM conversations ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d["used_cloud"] = bool(d.get("used_cloud", 0))
        d["complexity"] = d.get("complexity") or 0.0
        results.append(d)
    return results


def backup_db(dest_path: str) -> str:
    """Copy the live database to dest_path using SQLite's online backup API.
    Writes to a .tmp file first then atomically renames — a failed backup
    never leaves a corrupt destination file.
    """
    import os
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    src_conn = sqlite3.connect(DB_PATH)
    try:
        dest_conn = sqlite3.connect(str(tmp))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        src_conn.close()
    os.replace(str(tmp), str(dest))
    log.info("Database backed up to %s", dest)
    return str(dest)


def get_stats() -> dict:
    """Return a summary of the memory store state."""
    with _connect() as conn:
        stats = {}
        for layer in LAYERS:
            count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE layer = ? AND pruned = 0",
                (layer,),
            ).fetchone()[0]
            stats[layer] = count

        stats["total_active"] = sum(stats.values())
        stats["total_pruned"] = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE pruned = 1"
        ).fetchone()[0]
        stats["total_links"] = conn.execute(
            "SELECT COUNT(*) FROM memory_links"
        ).fetchone()[0]
        stats["total_conversations"] = conn.execute(
            "SELECT COUNT(*) FROM conversations"
        ).fetchone()[0]

        # Warn when tables are growing large — no auto-cleanup exists yet
        stats["warnings"] = []
        if stats["total_conversations"] > 10_000:
            stats["warnings"].append(f"conversations table has {stats['total_conversations']:,} rows — consider pruning old entries")
        if stats["total_pruned"] > 50_000:
            stats["warnings"].append(f"prune_log has {stats['total_pruned']:,} entries — consider archiving")

    return stats


# ─────────────────────────────────────────────
# CLI (quick test)
# ─────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    # Add some test memories
    m1 = add_memory(
        "Transformer architecture is good at processing and generating output but has no persistent state.",
        layer="recent",
        source="background_loop",
        tags=["transformer", "architecture"],
    )

    m2 = add_memory(
        "The LLM acts as the DNA of the system — foundational knowledge, not the active mind.",
        layer="recent",
        source="background_loop",
        tags=["LLM", "architecture", "vespera"],
    )

    m3 = add_memory(
        "Persistent loop stays running in background; cleanup crew runs in parallel to prevent hallucinations.",
        layer="recent",
        source="background_loop",
        tags=["persistent_loop", "cleanup_crew"],
    )

    # Link related memories
    link_memories(m1, m2, relationship="related", strength=0.8)
    link_memories(m2, m3, relationship="expands", strength=0.9)

    # Promote one to validated
    promote_memory(m2, new_trust_score=0.7)

    # Log a conversation
    add_conversation("user", "What should the persistent loop do when idle?")
    add_conversation("assistant", "Lightly cycle through recent context to maintain continuity.")

    # Print stats
    log.info("Memory store stats:")
    for k, v in get_stats().items():
        log.info("  %s: %s", k, v)

    # Show linked memories
    log.info("Memories linked to m1:")
    for m in get_linked_memories(m1):
        log.info("  [%s] %s...", m['relationship'], m['content'][:60])
