"""
Vespera Seed Injector
---------------------
Reads the three seed files and injects them as core memories into Vespera's DB.
Run once to give Vespera real context about Alfred, his projects, and Agora knowledge.

Usage:
    python3 seed_inject.py          # inject all, skip already-seeded
    python3 seed_inject.py --reset  # wipe existing seed memories and re-inject
"""

import os
import re
import sys
import json
from pathlib import Path

# Must run from vespera directory
sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from memory.store import init_db, add_memory, get_memories
from utils import get_logger

log = get_logger("seed_inject")
SEED_DIR = Path(__file__).parent / "seed"


def _already_seeded() -> set[str]:
    """Return set of seed source tags already in the DB."""
    mems = get_memories(layer="core", limit=5000)
    return {m["source"] for m in mems if m["source"].startswith("seed:")}


def _wipe_seed_memories():
    """Remove all seed memories from the DB."""
    import sqlite3
    db_path = Path(__file__).parent / "memory" / "vespera.db"
    with sqlite3.connect(db_path) as conn:
        deleted = conn.execute(
            "DELETE FROM memories WHERE source LIKE 'seed:%'"
        ).rowcount
        conn.commit()
    log.info("Wiped %d seed memories.", deleted)


def inject_alfred_profile():
    """Parse alfred-profile.md into focused memory chunks."""
    path = SEED_DIR / "alfred-profile.md"
    if not path.exists():
        log.warning("alfred-profile.md not found — skipping.")
        return 0

    text = path.read_text()
    source = "seed:alfred-profile"
    count = 0

    # Split on ## headings — each section becomes one memory
    sections = re.split(r'\n## ', text)
    for section in sections:
        section = section.strip()
        if not section or section.startswith("#"):
            continue
        # First line is the heading
        lines = section.split("\n", 1)
        heading = lines[0].strip("# ").strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if not body:
            continue

        content = f"[About Alfred — {heading}]\n{body}"
        add_memory(content=content, layer="core", source=source, trust_score=0.9,
                   tags=["alfred", "profile", heading.lower().replace(" ", "-")])
        count += 1
        log.info("  Profile: %s", heading)

    return count


def inject_projects():
    """Parse projects-history.md — each project becomes one memory."""
    path = SEED_DIR / "projects-history.md"
    if not path.exists():
        log.warning("projects-history.md not found — skipping.")
        return 0

    text = path.read_text()
    source = "seed:projects"
    count = 0

    # Split on ## headings (project blocks)
    sections = re.split(r'\n## ', text)
    for section in sections:
        section = section.strip()
        if not section or section.startswith("#"):
            continue
        lines = section.split("\n", 1)
        project_name = lines[0].strip("# ").strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if not body or len(body) < 50:
            continue

        content = f"[Project: {project_name}]\n{body}"
        add_memory(content=content, layer="core", source=source, trust_score=0.9,
                   tags=["project", project_name.lower().replace(" ", "-")])
        count += 1
        log.info("  Project: %s", project_name)

    return count


def inject_agora():
    """Parse agora-knowledge.md — each entry becomes one memory."""
    path = SEED_DIR / "agora-knowledge.md"
    if not path.exists():
        log.warning("agora-knowledge.md not found — skipping.")
        return 0

    text = path.read_text()
    source = "seed:agora"
    count = 0

    # Split on ## headings
    sections = re.split(r'\n## ', text)
    for section in sections:
        section = section.strip()
        if not section or section.startswith("#"):
            continue
        lines = section.split("\n", 1)
        title = lines[0].strip("# ").strip()
        body = ""
        if len(lines) > 1:
            # Strip the metadata lines (_Tags:_ etc) and get to the content
            raw_body = lines[1].strip()
            # Remove ---\n separators
            raw_body = raw_body.replace("\n---\n", "").replace("---", "").strip()
            # Remove metadata lines starting with _
            content_lines = [l for l in raw_body.split("\n") if not l.strip().startswith("_")]
            body = "\n".join(content_lines).strip()

        if not body or len(body) < 30:
            continue

        content = f"[Agora Knowledge — {title}]\n{body}"
        # Keep Agora entries shorter — they're reference material not personal context
        add_memory(content=content, layer="validated", source=source, trust_score=0.85,
                   tags=["agora", "knowledge"])
        count += 1

    log.info("  Agora: %d entries", count)
    return count


def main():
    reset = "--reset" in sys.argv
    init_db()

    if reset:
        print("Resetting seed memories...")
        _wipe_seed_memories()

    seeded = _already_seeded()

    total = 0

    if "seed:alfred-profile" not in seeded:
        print("Injecting alfred-profile.md...")
        n = inject_alfred_profile()
        print(f"  → {n} memories added")
        total += n
    else:
        print("Alfred profile already seeded — skipping. (use --reset to re-inject)")

    if "seed:projects" not in seeded:
        print("Injecting projects-history.md...")
        n = inject_projects()
        print(f"  → {n} memories added")
        total += n
    else:
        print("Projects already seeded — skipping.")

    if "seed:agora" not in seeded:
        print("Injecting agora-knowledge.md...")
        n = inject_agora()
        print(f"  → {n} memories added")
        total += n
    else:
        print("Agora knowledge already seeded — skipping.")

    print(f"\nDone. {total} memories injected into core/validated layers.")
    print("Vespera's background loop will now have real context to work from.")


if __name__ == "__main__":
    main()
