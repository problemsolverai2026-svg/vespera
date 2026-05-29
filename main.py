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
        print("[Vespera] ✅ Ollama is running.")
        return True
    except Exception:
        print("[Vespera] ⚠️  Ollama is not running!")
        print("[Vespera]    Open the Ollama app or run: ollama serve")
        print("[Vespera]    Continuing in cloud-only mode.")
        return False


# ─────────────────────────────────────────────
# COMPONENT RUNNERS
# ─────────────────────────────────────────────

def run_background_loop():
    from background_loop import think
    from memory.store import add_memory
    print(f"[BackgroundLoop] Started — interval: {BACKGROUND_LOOP_INTERVAL}s")
    while not _shutdown.is_set():
        try:
            thought = think()
            if thought:
                mem_id = add_memory(content=thought, layer="recent", source="background_loop")
                print(f"[BackgroundLoop] Thought saved ({mem_id[:8]}): {thought[:80]}...")
        except Exception as e:
            print(f"[BackgroundLoop] Error: {e}")
        _shutdown.wait(BACKGROUND_LOOP_INTERVAL)
    print("[BackgroundLoop] Stopped.")


def run_cleanup_crew():
    from cleanup_crew import run_cleanup
    print(f"[CleanupCrew] Started — interval: {CLEANUP_INTERVAL}s")
    while not _shutdown.is_set():
        try:
            run_cleanup()
        except Exception as e:
            print(f"[CleanupCrew] Error: {e}")
        _shutdown.wait(CLEANUP_INTERVAL)
    print("[CleanupCrew] Stopped.")


def run_periodic_pruning():
    from periodic_pruning import run_pruning
    interval = PRUNING_INTERVAL_DAYS * 24 * 60 * 60
    print(f"[PeriodicPruning] Started — every {PRUNING_INTERVAL_DAYS} days")
    while not _shutdown.is_set():
        try:
            run_pruning()
        except Exception as e:
            print(f"[PeriodicPruning] Error: {e}")
        _shutdown.wait(interval)
    print("[PeriodicPruning] Stopped.")


def run_scheduler():
    from scheduler import run as scheduler_run
    scheduler_run(_shutdown)


def print_status():
    while not _shutdown.is_set():
        _shutdown.wait(600)
        if not _shutdown.is_set():
            stats = get_stats()
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"\n[Vespera] {ts} — working:{stats['working']} recent:{stats['recent']} validated:{stats['validated']} core:{stats['core']}\n")


# ─────────────────────────────────────────────
# SHUTDOWN
# ─────────────────────────────────────────────

def handle_shutdown(sig, frame):
    print("\n[Vespera] Shutting down gracefully...")
    _shutdown.set()

signal.signal(signal.SIGINT,  handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


# ─────────────────────────────────────────────
# TEST MODE
# ─────────────────────────────────────────────

def run_test():
    print("[Vespera] Running test mode — one pass each...\n")
    from background_loop import think
    from cleanup_crew import run_cleanup
    from periodic_pruning import run_pruning
    from memory.store import add_memory

    print("--- Background Loop ---")
    thought = think()
    if thought:
        add_memory(content=thought, layer="recent", source="background_loop")
        print(f"Thought: {thought[:100]}...")
    else:
        print("No thought generated.")

    print("\n--- Cleanup Crew ---")
    run_cleanup()

    print("\n--- Periodic Pruning ---")
    run_pruning()

    print("\n--- Stats ---")
    for k, v in get_stats().items():
        print(f"  {k}: {v}")

    print("\n[Vespera] Test complete.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    init_db()

    if "--test" in sys.argv:
        run_test()
        return

    print("=" * 50)
    print("  🌙 Vespera Persistent AI Memory System")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)

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

    print("[Vespera] All components running. Press Ctrl+C to stop.\n")

    while not _shutdown.is_set():
        _shutdown.wait(1)

    print("[Vespera] Goodbye.")


if __name__ == "__main__":
    main()
