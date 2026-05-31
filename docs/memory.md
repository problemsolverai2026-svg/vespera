# How Vespera's Memory Works

Vespera uses a layered memory system — a "nesting doll" — where memories earn their way to permanence through progressive trust levels. This prevents hallucinated or low-quality thoughts from polluting long-term context.

---

## The Four Layers

```
┌─────────────────────────────────────────────────┐
│  CORE  — permanent, highest trust               │
│  ┌───────────────────────────────────────────┐  │
│  │  VALIDATED  — reviewed, promoted           │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  RECENT  — fresh, unreviewed        │  │  │
│  │  │  ┌───────────────────────────────┐  │  │  │
│  │  │  │  WORKING  — active session    │  │  │  │
│  │  │  └───────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

| Layer | What it holds | Trust score | Auto-pruned? |
|---|---|---|---|
| `working` | Active conversation context | 0.0 | Yes — ephemeral |
| `recent` | Fresh background thoughts | 0.0–0.3 | Yes — reviewed every 5 min |
| `validated` | Cleanup-approved memories | 0.3–0.7 | Yes — reviewed every 3 days |
| `core` | Permanent, deeply trusted | 0.7–1.0 | Never |

---

## How a Memory Is Born

Every 3 minutes, the **Background Loop** wakes up, reads recent conversations, and tries to generate one focused thought. It uses the local Ollama model for this — no cloud API needed.

If it encounters something technical it doesn't understand, it does a **web search** instead of guessing, then summarizes the result into a thought.

If it has nothing new to add, it says `NOTHING_NEW` and goes back to sleep.

The thought is saved to the `recent` layer with trust score `0.0`.

---

## How Memories Are Promoted

### Step 1 — Cleanup Crew (every 5 minutes)

Reviews `recent` memories one at a time. For each memory, it asks the local model:

> "Is this thought coherent, useful, and non-repetitive?"

- **Keep** → promoted to `validated`, trust score raised to `0.5`
- **Delete** → soft-deleted, logged to `prune_log`

This is the main defense against the background loop getting stuck in repetitive loops.

### Step 2 — Periodic Pruning (every 3 days)

Reviews `validated` memories against `core` memories. Applies a stricter bar:

> "Is this genuinely valuable enough to keep permanently?"

- **Promote** → moved to `core`, trust score raised to `0.95`
- **Keep** → stays in `validated`
- **Delete** → soft-deleted

---

## How Memory Is Used

When you send a message, the handoff logic pulls:
- Up to 8 `core` memories (or `validated` if no core yet)
- The 20 most recent conversation turns

These are injected into the prompt as context before the model responds. This is how Vespera "remembers" — it's not magic, it's carefully curated context.

---

## Trust Scores

Every memory has a trust score from `0.0` to `1.0`:

| Score | Meaning |
|---|---|
| 0.0 | Fresh, unreviewed |
| 0.5 | Passed cleanup review |
| 0.95 | Passed deep pruning — core memory |

Trust scores are not currently used for weighted retrieval (all memories in a layer are treated equally), but they're stored for future use.

---

## Memory Links

Memories can be linked to each other with a relationship type:

| Relationship | Meaning |
|---|---|
| `related` | These two memories are about the same topic |
| `expands` | One memory builds on another |
| `contradicts` | These memories conflict — needs resolution |
| `references` | One memory mentions the other |

Links aren't currently used automatically but are available via the API (`/api/memories`) for visualization and future retrieval improvements.

---

## What Gets Pruned

Memories are soft-deleted (marked `pruned=1`) — they're never actually removed from the database. A full audit trail is kept in the `prune_log` table with the reason and which component pruned it.

This means if the cleanup crew makes a bad call, the memory isn't truly lost.

---

## Database

All memory lives in a single SQLite file: `memory/vespera.db`

Tables:
- `memories` — the nesting doll store
- `memory_links` — relationships between memories
- `conversations` — full conversation history (never pruned)
- `prune_log` — audit trail of everything removed

WAL mode is enabled so multiple processes can read and write without locking each other out.

---

## Backup

You can back up the database any time via the API:

```bash
curl -X POST http://localhost:5055/api/backup
```

> **Note:** If your API is running on a different port (check the terminal output from `./start.sh`), replace `5055` with the actual port.

Backups are saved to `backups/vespera_YYYYMMDD_HHMMSS.db` inside the Vespera folder.
