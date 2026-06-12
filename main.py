"""
Vespera — Launcher
------------------
Initializes the database and starts all components as threads.

For production use, each component has its own LaunchAgent (macOS)
or systemd service (Linux) so they run independently and restart on crash.

For manual use, run: ./start.sh
"""

import sys
import os
import time
import threading
import signal
from pathlib import Path
from utils import get_logger, _sanitize

log = get_logger("vespera")
import requests as req
from datetime import datetime, timezone
from memory.store import init_db, get_stats
from config import BACKGROUND_LOOP_INTERVAL, CLEANUP_INTERVAL, PRUNING_INTERVAL_DAYS

_shutdown = threading.Event()
_lockfd = None  # module-level ref keeps fd open (and lock held) for process lifetime


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
    from background_loop import run_loop
    run_loop(_shutdown)


def run_cleanup_crew():
    from cleanup_crew import run_loop
    run_loop(_shutdown)


def run_periodic_pruning():
    from periodic_pruning import run_loop
    run_loop(_shutdown)


def run_scheduler():
    from scheduler import run as scheduler_run, register_callback
    # Wire Telegram delivery so reminders actually get sent.
    # send_reminder is synchronous (uses asyncio.run internally) — safe to call from callback pool.
    try:
        from telegram_bot import send_reminder
        register_callback(send_reminder)
        log.info("Telegram reminder delivery registered.")
    except Exception as e:
        log.warning("Telegram callback not registered (reminders will fire silently): %s", e)
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
    # Re-register default SIGTERM so a second signal kills immediately
    # instead of being ignored if the handler is still on the stack.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT,  signal.SIG_DFL)


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
        thought = _sanitize(thought, 500)  # sanitize model output before storage
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
    # ── flock-based lock — atomic, SIGKILL-safe, no TOCTOU ──────────────────────
    global _lockfd
    import fcntl
    lock_file = Path(__file__).parent / ".main.lock"
    # Open without truncating — truncate AFTER acquiring lock so we never
    # wipe a running process's PID before confirming it is free.
    _lockfd = open(lock_file, 'a+')
    try:
        fcntl.flock(_lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        _lockfd.close()
        log.error("Already running. Exiting.")
        raise SystemExit(0)
    # Lock acquired — now safe to write our PID
    _lockfd.seek(0)
    _lockfd.truncate()
    _lockfd.write(str(os.getpid()))
    _lockfd.flush()
    # ───────────────────────────────────────────────────────────────────

    init_db()

    if "--test" in sys.argv:
        try:
            run_test()
        finally:
            _lockfd.close()
            lock_file.unlink(missing_ok=True)
        return

    log.info("=" * 50)
    log.info("  🌙 Vespera Persistent AI Memory System")
    log.info("  Started: %s", datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))
    log.info("=" * 50)

    # Register signal handlers BEFORE check_ollama() so Ctrl-C during a hang
    # still triggers graceful shutdown and cleans up the lock file.
    signal.signal(signal.SIGINT,  handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

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

    try:
        _shutdown.wait()  # block until signal fires
    finally:
        # Give daemon threads a brief grace period to finish in-flight work
        _shutdown.set()
        for t in threads:
            t.join(timeout=5)
        # Release lock and clean up — guard against early SIGINT before
        # _lockfd is assigned (would raise AttributeError swallowed silently,
        # leaving a stale lock file).
        try:
            if _lockfd is not None:
                _lockfd.close()
            lock_file.unlink(missing_ok=True)
        except Exception:
            pass

    log.info("Goodbye.")
    # Explicit exit — ensures the process terminates even if a non-daemon
    # thread is still alive (e.g. blocked in a requests call or scheduler).
    sys.exit(0)


if __name__ == "__main__":
    main()
