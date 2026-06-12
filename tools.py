"""
Vespera Tool Execution
----------------------
Gives the cloud model the ability to actually DO things on the machine.

Tools available:
  - shell      : run a shell command, get output
  - read_file  : read a file's contents
  - write_file : write content to a file

Security:
  - VESPERA_ALLOW_SHELL=true required to enable shell (off by default)
  - ALLOW_PATHS restricts both the working directory AND all path arguments
    in shell commands (absolute, home-relative, and `..` traversal are all blocked).
  - The binary path (argv[0]) is also path-checked.
  - read_file and write_file ARE path-checked via path_allowed().
"""

import os
import subprocess
from pathlib import Path

# ─────────────────────────────────────────────
# SECURITY CONFIG — pulled from central security module
# ─────────────────────────────────────────────

from security import ALLOW_SHELL, ALLOW_PATHS, path_allowed as _path_allowed_fn
HOME = str(Path.home())

# ─────────────────────────────────────────────
# TOOL DEFINITIONS (sent to Claude)
# ─────────────────────────────────────────────

TOOL_DEFINITIONS = []

if ALLOW_SHELL:
    TOOL_DEFINITIONS.append({
        "name": "shell",
        "description": "Run a shell command on the user's machine and return the output. Use for system tasks, file operations, running scripts, checking status of services, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run."
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory. Defaults to home directory."
                }
            },
            "required": ["command"]
        }
    })

# read_file and write_file are gated on ALLOW_SHELL — file access is
# security-equivalent to shell access and should require the same opt-in.
if ALLOW_SHELL:
    TOOL_DEFINITIONS.append({
        "name": "read_file",
        "description": "Read the contents of a file on the user's machine.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or home-relative path to the file."
                }
            },
            "required": ["path"]
        }
    })

    TOOL_DEFINITIONS.append({
        "name": "write_file",
        "description": "Write content to a file on the user's machine. Creates the file if it doesn't exist.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or home-relative path to the file."
                },
                "content": {
                    "type": "string",
                    "description": "Content to write."
                }
            },
            "required": ["path", "content"]
        }
    })


# ─────────────────────────────────────────────
# SECURITY CHECK
# ─────────────────────────────────────────────

def _path_allowed(path: str) -> bool:
    return _path_allowed_fn(path)


# ─────────────────────────────────────────────
# TOOL RUNNERS
# ─────────────────────────────────────────────

def run_shell(command: str, workdir: str = None) -> str:
    if not ALLOW_SHELL:
        return "Error: shell execution is disabled. Set VESPERA_ALLOW_SHELL=true in .env to enable."
    # Use shell=False with shlex.split() — eliminates all shell injection vectors.
    # No blocklist needed: without a shell interpreter there is nothing to inject into.
    import shlex
    try:
        args = shlex.split(command)
    except ValueError as e:
        return f"Error: invalid command syntax: {e}"
    if not args:
        return "Error: empty command."

    # Path-check any arguments that look like absolute or home-relative paths.
    # Since shell=False is used there is no shell expansion, so direct path
    # arguments like `cat /etc/passwd` or `ls ~/secret` are caught here.
    # Option-style args (e.g. --output=/etc/foo) are also checked.
    # Determine the effective working directory first so relative args resolve correctly.
    # Default to ALLOW_PATHS[0] (sandbox root) if no workdir given; fall back to HOME
    # only if ALLOW_PATHS is empty (shouldn't happen in normal config).
    if not ALLOW_PATHS:
        return "Error: no allowed paths configured — shell access is fully restricted."
    sandbox_root = str(Path(ALLOW_PATHS[0]).resolve())

    cwd = sandbox_root
    if workdir:
        resolved_wd = str(Path(workdir.replace("~", HOME, 1)).expanduser())
        if not _path_allowed(resolved_wd):
            return "Error: workdir not in allowed paths."
        if not os.path.isdir(resolved_wd):
            return "Error: workdir does not exist or is not a directory."
        cwd = resolved_wd

    # Check ALL arguments (absolute, home-relative, dotdot, AND plain relative)
    # resolved against the actual cwd so paths like `../secret.txt` are caught
    # correctly relative to where the command will actually run.
    for i, arg in enumerate(args):
        val = arg.split("=", 1)[-1] if (i > 0 and "=" in arg) else arg
        if i > 0 and val.startswith("-"):
            continue  # skip option flags like -v, --output
        candidate = Path(val.replace("~", HOME, 1))
        if not candidate.is_absolute():
            candidate = Path(cwd) / candidate
        resolved_arg = str(candidate.resolve())
        if not _path_allowed(resolved_arg):
            return f"Error: path not in allowed paths: {val}"
    try:
        result = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            return "(no output)"
        if len(output) > 32_000:
            output = output[:32_000] + "\n[output truncated — exceeded 32,000 chars]"
        return output
    except FileNotFoundError:
        return f"Error: command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds."
    except Exception as e:
        return f"Error: {e}"


_MAX_READ_BYTES  = 512 * 1024   # 512 KB
_MAX_WRITE_BYTES = 1 * 1024 * 1024  # 1 MB

def run_read_file(path: str) -> str:
    resolved = path.replace("~", HOME, 1)
    if not _path_allowed(resolved):
        return f"Error: path not allowed: {resolved}"
    try:
        p = Path(resolved)
        size = p.stat().st_size
        if size > _MAX_READ_BYTES:
            return f"Error: file too large ({size} bytes, max {_MAX_READ_BYTES})."
        return p.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"Error: file not found: {resolved}"
    except Exception as e:
        return f"Error: {e}"


def run_write_file(path: str, content: str) -> str:
    resolved = path.replace("~", HOME, 1)
    if not _path_allowed(resolved):
        return f"Error: path not allowed: {resolved}"
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return f"Error: content too large (max {_MAX_WRITE_BYTES} bytes)."
    try:
        p = Path(resolved)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            import logging
            logging.getLogger("tools").warning("Overwriting existing file: %s", resolved)
        p.write_text(content, encoding="utf-8")
        return f"Written: {resolved}"
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────────
# CALENDAR TOOL
# ─────────────────────────────────────────────

TOOL_DEFINITIONS.append({
    "name": "get_calendar",
    "description": "Get upcoming calendar events from the user's Apple Calendar. Returns events for the next N days (default 7).",
    "input_schema": {
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days ahead to look. Default 7."
            }
        },
        "required": []
    }
})


def run_get_calendar(days: int = 7) -> str:
    """Read upcoming events — tries Google Calendar (gog) first, falls back to Apple Calendar."""
    import subprocess
    from datetime import datetime, timezone, timedelta
    days = max(1, min(int(days), 90))

    # ── Google Calendar via gog ──────────────────────────────────────
    try:
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)
        from_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_str   = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
        result = subprocess.run(
            ["gog", "calendar", "events", "primary",
             "--from", from_str, "--to", to_str, "--plain"],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip()
        if result.returncode == 0 and output and output.lower() != "no events":
            return f"Upcoming events (next {days} days — Google Calendar):\n{output}"
        if result.returncode == 0:
            google_result = f"No events in Google Calendar for the next {days} days."
        else:
            google_result = f"Google Calendar unavailable: {result.stderr.strip()[:120]}"
    except subprocess.TimeoutExpired:
        google_result = "Google Calendar timed out."
    except FileNotFoundError:
        google_result = "gog not found — Google Calendar unavailable."
    except Exception as e:
        google_result = f"Google Calendar error: {e}"

    # ── Apple Calendar fallback via AppleScript ──────────────────────
    script = f'''
tell application "Calendar"
    set output to ""
    set today to current date
    set cutoff to today + ({days} * days)
    repeat with cal in calendars
        repeat with ev in (every event of cal whose start date >= today and start date <= cutoff)
            set evTitle to summary of ev
            set evStart to start date of ev as string
            set calName to name of cal
            set output to output & calName & " | " & evTitle & " | " & evStart & "\n"
        end repeat
    end repeat
    if output is "" then
        return "EMPTY"
    end if
    return output
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15
        )
        apple_out = result.stdout.strip()
        if result.returncode == 0 and apple_out and apple_out != "EMPTY":
            lines = [l for l in apple_out.split("\n") if l.strip()]
            lines.sort(key=lambda l: l.split(" | ")[2] if len(l.split(" | ")) > 2 else l)
            return f"Upcoming events (next {days} days — Apple Calendar):\n" + "\n".join(lines)
    except Exception:
        pass

    return f"{google_result} Apple Calendar also empty. No upcoming events found."


# ─────────────────────────────────────────────
# REMINDER TOOLS
# ─────────────────────────────────────────────

def run_set_reminder(message: str, when: str, recur: str = None) -> str:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    import re
    tz = ZoneInfo(os.getenv("VESPERA_TIMEZONE", "America/Chicago"))
    now = datetime.now(tz)

    fire_at = None
    # Try ISO 8601 first
    try:
        dt = datetime.fromisoformat(when)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        fire_at = dt
    except ValueError:
        pass

    # Try dateutil parser as fallback
    if fire_at is None:
        try:
            from dateutil import parser as du_parser
            dt = du_parser.parse(when, default=now)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            fire_at = dt
        except Exception:
            pass

    if fire_at is None:
        return f"Error: could not parse time '{when}'. Use a format like '8:45pm', 'today at 8:45am', or '2026-06-04T20:30:00'."

    # If time is in the past, bump to tomorrow (for same-day times that already passed)
    if fire_at <= now:
        from dateutil.relativedelta import relativedelta
        fire_at = fire_at + relativedelta(days=1)

    try:
        from scheduler import add_reminder
        rid = add_reminder(message, fire_at, recur=recur)
        local_str = fire_at.astimezone(tz).strftime("%Y-%m-%d %I:%M %p %Z")
        return f"Reminder set: '{message}' at {local_str} (id: {rid})"
    except Exception as e:
        return f"Error setting reminder: {e}"


def run_list_reminders() -> str:
    try:
        from scheduler import list_reminders
        from datetime import timezone
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("VESPERA_TIMEZONE", "America/Chicago"))
        reminders = list_reminders()
        if not reminders:
            return "No active reminders."
        lines = []
        for r in reminders:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(r["fire_at"]).astimezone(tz)
                time_str = dt.strftime("%Y-%m-%d %I:%M %p %Z")
            except Exception:
                time_str = r["fire_at"]
            recur = f" (recurring: {r['recur']})" if r.get("recur") else ""
            lines.append(f"- [{r['id'][:8]}] {r['message']} at {time_str}{recur}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing reminders: {e}"


def run_cancel_reminder(rid: str) -> str:
    try:
        from scheduler import cancel_reminder
        ok = cancel_reminder(rid)
        return "Reminder cancelled." if ok else f"No active reminder found with id '{rid}'."
    except Exception as e:
        return f"Error cancelling reminder: {e}"


# ─────────────────────────────────────────────
# EXPLICIT EXPORTS
# ─────────────────────────────────────────────

__all__ = ["TOOL_DEFINITIONS", "run_tool"]


# ─────────────────────────────────────────────
# DISPATCH
# ─────────────────────────────────────────────

def run_tool(name: str, inputs: dict) -> str:
    if name == "get_calendar":
        return run_get_calendar(int(inputs.get("days", 7)))
    if name == "shell":
        return run_shell(inputs.get("command", ""), inputs.get("workdir"))
    if name == "read_file":
        return run_read_file(inputs.get("path", ""))
    if name == "write_file":
        return run_write_file(inputs.get("path", ""), inputs.get("content", ""))
    if name == "set_reminder":
        return run_set_reminder(inputs.get("message", ""), inputs.get("when", ""), inputs.get("recur"))
    if name == "list_reminders":
        return run_list_reminders()
    if name == "cancel_reminder":
        return run_cancel_reminder(inputs.get("id", ""))
    return f"Error: unknown tool '{name}'"
