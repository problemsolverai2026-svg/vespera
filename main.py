"""
Vespera — Launcher
------------------
Initializes the database and starts all components as threads.

For production use, each component has its own LaunchAgent (macOS)
or systemd service (Linux) so they run independently and restart on crash.

For manual use, run: ./start.sh
"""

import sys
import time
import threading
import signal
from utils import get_logger

log = get_logger("vespera")
import requests as req
from datetime import datetime, timezone
from memory.store import init_db, get_stats
from config import BACKGROUND_LOOP_INTERVAL, CLEANUP_INTERVAL, PRUNING_INTERVAL_DAYS

_shutdown = threading.Event()


# ─────────────────────────────────────────────
# HEALTH CHECKS
# ─────────────────────────────────────────────

def check_ollama() -> bool:
    try:
        req.get("http://localhost:11434", timeout=3)
        log.info("✅ Ollama is running.")
        return True
    except Exception:
        log.warning("⚠️  Ollama is not running!")
        log.warning("   Open the Ollama app or run: ollama serve")
        log.warning("   Continuing in cloud-only mode.")
        return False


# ─────────────────────────────────────────────
# COMPONENT RUNNERS
# ─────────────────────────────────────────────

def run_background_loop():
    from background_loop import think
    from memory.store import add_memory
    log.info("BackgroundLoop started — interval: %ss", BACKGROUND_LOOP_INTERVAL)
    while not _shutdown.is_set():
        try:
            thought = think()
            if thought:
                mem_id = add_memory(content=thought, layer="recent", source="background_loop")
                log.info("BackgroundLoop thought saved (%s): %s...", mem_id[:8], thought[:80])
        except Exception as e:
            log.error("BackgroundLoop error: %s", e)
        _shutdown.wait(BACKGROUND_LOOP_INTERVAL)
    log.info("BackgroundLoop stopped.")


def run_cleanup_crew():
    from cleanup_crew import run_cleanup
    log.info("CleanupCrew started — interval: %ss", CLEANUP_INTERVAL)
    while not _shutdown.is_set():
        try:
            run_cleanup()
        except Exception as e:
            log.error("CleanupCrew error: %s", e)
        _shutdown.wait(CLEANUP_INTERVAL)
    log.info("CleanupCrew stopped.")


def run_periodic_pruning():
    from periodic_pruning import run_pruning
    interval = PRUNING_INTERVAL_DAYS * 24 * 60 * 60
    log.info("PeriodicPruning started — every %d days", PRUNING_INTERVAL_DAYS)
    while not _shutdown.is_set():
        try:
            run_pruning()
        except Exception as e:
            log.error("PeriodicPruning error: %s", e)
        _shutdown.wait(interval)
    log.info("PeriodicPruning stopped.")


def run_scheduler():
    from scheduler import run as scheduler_run
    scheduler_run(_shutdown)


def print_status():
    while not _shutdown.is_set():
        _shutdown.wait(600)
        if not _shutdown.is_set():
            stats = get_stats()
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            log.info("%s — working:%d recent:%d validated:%d core:%d", ts, stats['working'], stats['recent'], stats['validated'], stats['core'])


# ─────────────────────────────────────────────
# SHUTDOWN
# ─────────────────────────────────────────────

def handle_shutdown(sig, frame):
    log.info("Shutting down gracefully...")
    _shutdown.set()

signal.signal(signal.SIGINT,  handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


# ─────────────────────────────────────────────
# TEST MODE
# ─────────────────────────────────────────────

def run_test():
    log.info("Running test mode — one pass each...")
    from background_loop import think
    from cleanup_crew import run_cleanup
    from periodic_pruning import run_pruning
    from memory.store import add_memory

    log.info("--- Background Loop ---")
    thought = think()
    if thought:
        add_memory(content=thought, layer="recent", source="background_loop")
        log.info("Thought: %s...", thought[:100])
    else:
        log.info("No thought generated.")

    log.info("--- Cleanup Crew ---")
    run_cleanup()

    log.info("--- Periodic Pruning ---")
    run_pruning()

    log.info("--- Stats ---")
    for k, v in get_stats().items():
        log.info("  %s: %s", k, v)

    log.info("Test complete.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    init_db()

    if "--test" in sys.argv:
        run_test()
        return

    log.info("=" * 50)
    log.info("  🌙 Vespera Persistent AI Memory System")
    log.info("  Started: %s", datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    log.info("=" * 50)

    check_ollama()

    threads = [
        threading.Thread(target=run_background_loop, daemon=True, name="BackgroundLoop"),
        threading.Thread(target=run_cleanup_crew,    daemon=True, name="CleanupCrew"),
        threading.Thread(target=run_periodic_pruning,daemon=True, name="PeriodicPruning"),
        threading.Thread(target=run_scheduler,       daemon=True, name="Scheduler"),
        threading.Thread(target=print_status,        daemon=True, name="StatusPrinter"),
    ]

    for t in threads:
        t.start()

    log.info("All components running. Press Ctrl+C to stop.")

    while not _shutdown.is_set():
        _shutdown.wait(1)

    log.info("Goodbye.")


if __name__ == "__main__":
    main()
