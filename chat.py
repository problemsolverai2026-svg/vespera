"""
Vespera Chat Interface
----------------------
Simple CLI to talk to Vespera. The front door.
Logs every message to conversation history so the background loop
has something to think about.

Usage:
  python3 chat.py
"""

from memory.store import init_db, add_conversation, get_stats
from handoff import handle_message
from utils import get_logger, _sanitize

log = get_logger("chat")

BANNER = """
╔══════════════════════════════════════╗
║           V E S P E R A             ║
║    Persistent AI Memory System       ║
║  Type 'exit' to quit, 'stats' for   ║
║  memory status, 'help' for commands ║
╚══════════════════════════════════════╝
"""

HELP = """
Commands:
  stats    — show memory layer counts
  clear    — clear screen
  exit     — quit
  
Anything else is sent to Vespera.
"""


def main():
    init_db()
    print(BANNER)

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[Vespera] Goodbye.")
            break

        user_input = user_input[:8000]  # match API limit

        if not user_input:
            continue

        # Built-in commands
        if user_input.lower() == "exit":
            print("[Vespera] Goodbye.")
            break

        if user_input.lower() == "stats":
            stats = get_stats()
            print(f"\n  working:    {stats['working']}")
            print(f"  recent:     {stats['recent']}")
            print(f"  validated:  {stats['validated']}")
            print(f"  core:       {stats['core']}")
            print(f"  pruned:     {stats['total_pruned']}")
            print(f"  links:      {stats['total_links']}")
            print(f"  convs:      {stats['total_conversations']}\n")
            continue

        if user_input.lower() == "help":
            print(HELP)
            continue

        if user_input.lower() == "clear":
            print("\033[H\033[J", end="")
            continue

        # Sanitize before storing — same as the API endpoint does
        safe_input = _sanitize(user_input, 8000)
        if not safe_input:
            print("[Message contained only invalid characters — skipped]")
            continue
        add_conversation(role="user", content=safe_input)

        # Fire-and-forget fact extraction
        from facts import extract_facts_async
        extract_facts_async(safe_input)

        # Get response
        try:
            result = handle_message(safe_input)
            response = result.get("response", "") or "(no response)"
            handled   = result.get("handled_by", "")
            complexity = result.get("complexity", 0.0)
            tag = f"[local {complexity:.0%}]" if handled == "local" else f"[cloud {complexity:.0%}]"
            print(f"\nVespera {tag}: {response}\n")
            # Sanitize response before storing to conversation history
            safe_response = _sanitize(response, len(response))
            add_conversation(role="assistant", content=safe_response)
        except Exception as e:
            print(f"\n[Error: {e} — try again]\n")
            log.error("CLI handle_message error: %s", e)


if __name__ == "__main__":
    main()
