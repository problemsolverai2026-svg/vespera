-- Vespera Memory Storage Schema
-- Nesting doll architecture: working → recent → validated → core

-- LAYERS (innermost = most trusted):
--   core      : passed Trusted AI 95% threshold, never auto-pruned
--   validated : cleanup crew approved, candidate for core
--   recent    : fresh background loop output, not yet reviewed
--   working   : active conversation context, ephemeral

CREATE TABLE IF NOT EXISTS memories (
    id            TEXT PRIMARY KEY,          -- UUID
    content       TEXT NOT NULL,             -- the actual memory text
    layer         TEXT NOT NULL              -- core | validated | recent | working
                  CHECK(layer IN ('core', 'validated', 'recent', 'working')),
    source        TEXT NOT NULL,             -- background_loop | conversation | pruning | user
    trust_score   REAL DEFAULT 0.0,          -- 0.0 to 1.0 (1.0 = fully trusted)
    tags          TEXT DEFAULT '[]',         -- JSON array of topic tags
    created_at    TEXT NOT NULL,             -- ISO timestamp
    updated_at    TEXT NOT NULL,             -- ISO timestamp
    pruned        INTEGER DEFAULT 0          -- soft delete flag (0=active, 1=pruned)
);

-- Links between memories (the "shared file" connection Alfred described)
CREATE TABLE IF NOT EXISTS memory_links (
    id              TEXT PRIMARY KEY,
    memory_id_a     TEXT NOT NULL REFERENCES memories(id),
    memory_id_b     TEXT NOT NULL REFERENCES memories(id),
    relationship    TEXT NOT NULL            -- related | expands | contradicts | references
                    CHECK(relationship IN ('related', 'expands', 'contradicts', 'references')),
    strength        REAL DEFAULT 0.5,        -- 0.0 to 1.0 (how strong the connection is)
    created_at      TEXT NOT NULL
);

-- Conversation history (source of truth, never pruned)
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content     TEXT NOT NULL,
    summary     TEXT                         -- short summary generated after conversation
);

-- Pruning log (audit trail of what was removed and why)
CREATE TABLE IF NOT EXISTS prune_log (
    id          TEXT PRIMARY KEY,
    memory_id   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    pruned_by   TEXT NOT NULL,               -- cleanup_crew | periodic_pruning | manual
    pruned_at   TEXT NOT NULL,
    content     TEXT NOT NULL                -- keep a copy of what was pruned
);

-- Indexes for fast lookup
CREATE INDEX IF NOT EXISTS idx_memories_layer   ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_pruned  ON memories(pruned);
CREATE INDEX IF NOT EXISTS idx_links_a          ON memory_links(memory_id_a);
CREATE INDEX IF NOT EXISTS idx_links_b          ON memory_links(memory_id_b);
CREATE INDEX IF NOT EXISTS idx_conv_timestamp   ON conversations(timestamp);
