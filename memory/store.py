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
import uuid
from datetime import datetime, timezone
from pathlib import Path
from utils import get_logger

log = get_logger("memory.store")


DB_PATH = Path(__file__).parent / "vespera.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

LAYERS = ["working", "recent", "validated", "core"]
LAYER_ORDER = {layer: i for i, layer in enumerate(LAYERS)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # allows concurrent readers + one writer
    conn.execute("PRAGMA busy_timeout=5000")  # wait up to 5s instead of failing instantly
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize the database from schema."""
    schema = SCHEMA_PATH.read_text()
    with _connect() as conn:
        conn.executescript(schema)
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


def add_conversation(role: str, content: str, summary: str = None) -> str:
    """Log a conversation message. Returns its ID."""
    conv_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, timestamp, role, content, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, _now(), role, content, summary),
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
        conn.execute(
            "UPDATE memories SET layer = ?, trust_score = COALESCE(?, trust_score), updated_at = ? WHERE id = ?",
            (next_layer, new_trust_score, _now(), memory_id),
        )
        log.info("Promoted %s: %s → %s", memory_id[:8], current, next_layer)
        return True


def prune_memory(memory_id: str, reason: str, pruned_by: str = "cleanup_crew"):
    """Soft-delete a memory and log it."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT content FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()

        if not row:
            return

        conn.execute(
            "UPDATE memories SET pruned = 1, updated_at = ? WHERE id = ?",
            (_now(), memory_id),
        )
        conn.execute(
            """
            INSERT INTO prune_log (id, memory_id, reason, pruned_by, pruned_at, content)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), memory_id, reason, pruned_by, _now(), row["content"]),
        )
        log.info("Pruned %s (%s): %s", memory_id[:8], pruned_by, reason)


# ─────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────

def get_memories(
    layer: str = None,
    limit: int = 20,
    include_pruned: bool = False,
) -> list[dict]:
    """Fetch memories, optionally filtered by layer."""
    query = "SELECT * FROM memories WHERE 1=1"
    params = []

    if not include_pruned:
        query += " AND pruned = 0"
    if layer:
        query += " AND layer = ?"
        params.append(layer)

    query += " ORDER BY created_at DESC LIMIT ?"
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
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM conversations ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def backup_db(dest_path: str) -> str:
    """Copy the live database to dest_path using SQLite's online backup API."""
    import shutil
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    src_conn  = sqlite3.connect(DB_PATH)
    dest_conn = sqlite3.connect(dest)
    with dest_conn:
        src_conn.backup(dest_conn)
    src_conn.close()
    dest_conn.close()
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
