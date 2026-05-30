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
from utils import get_logger

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

        # Log user message
        add_conversation(role="user", content=user_input)

        # Get response
        try:
            result = handle_message(user_input)
            response = result.get("response", "")
            handled   = result.get("handled_by", "")
            complexity = result.get("complexity", 0.0)
            tag = f"[local {complexity:.0%}]" if handled == "local" else f"[cloud {complexity:.0%}]"
            print(f"\nVespera {tag}: {response}\n")
            # Log assistant response
            add_conversation(role="assistant", content=response)
        except Exception as e:
            print(f"\n[Error: {e} — try again]\n")
            log.error("CLI handle_message error: %s", e)


if __name__ == "__main__":
    main()
