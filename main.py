"""
Vespera — Persistent AI Memory System
--------------------------------------
Main launcher. Starts all components in parallel threads:
  - Background Loop    : thinks continuously, saves to memory
  - Cleanup Crew       : reviews recent memories, promotes or prunes
  - Periodic Pruning   : deep clean every 3-4 days

Usage:
  python3 main.py              # run everything
  python3 main.py --test       # single pass of each component, then exit
"""

import sys
import time
import threading
import signal
from datetime import datetime, timezone
from memory.store import init_db, get_stats
from config import BACKGROUND_LOOP_INTERVAL, CLEANUP_INTERVAL, PRUNING_INTERVAL_DAYS

# ─────────────────────────────────────────────
# SHUTDOWN HANDLER
# ─────────────────────────────────────────────

_shutdown = threading.Event()

def handle_shutdown(sig, frame):
    print("\n[Vespera] Shutting down gracefully...")
    _shutdown.set()

signal.signal(signal.SIGINT,  handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


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


# ─────────────────────────────────────────────
# STATUS PRINTER
# ─────────────────────────────────────────────

def print_status():
    """Print memory stats every 10 minutes."""
    while not _shutdown.is_set():
        _shutdown.wait(600)
        if not _shutdown.is_set():
            stats = get_stats()
            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"\n[Vespera] {ts} — working:{stats['working']} recent:{stats['recent']} validated:{stats['validated']} core:{stats['core']} pruned:{stats['total_pruned']}\n")


# ─────────────────────────────────────────────
# TEST MODE
# ─────────────────────────────────────────────

def run_test():
    """Single pass of each component — for quick verification."""
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

    print("\n--- Final Stats ---")
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
    print("  Vespera Persistent AI Memory System")
    print(f"  Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 50)

    threads = [
        threading.Thread(target=run_background_loop, daemon=True, name="BackgroundLoop"),
        threading.Thread(target=run_cleanup_crew,    daemon=True, name="CleanupCrew"),
        threading.Thread(target=run_periodic_pruning,daemon=True, name="PeriodicPruning"),
        threading.Thread(target=print_status,        daemon=True, name="StatusPrinter"),
    ]

    for t in threads:
        t.start()

    print("[Vespera] All components running. Press Ctrl+C to stop.\n")

    # Keep main thread alive
    while not _shutdown.is_set():
        _shutdown.wait(1)

    print("[Vespera] Goodbye.")


if __name__ == "__main__":
    main()
