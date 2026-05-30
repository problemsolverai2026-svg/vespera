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
  - Paths are restricted to user home directory by default
  - Set VESPERA_ALLOW_PATHS to expand allowed paths (comma-separated)
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

_SHELL_BLOCKLIST = [";", "&&", "||", "|", "$(", "`", ">", ">>", "<(", "2>"]

def run_shell(command: str, workdir: str = None) -> str:
    if not ALLOW_SHELL:
        return "Error: shell execution is disabled. Set VESPERA_ALLOW_SHELL=true in .env to enable."
    for token in _SHELL_BLOCKLIST:
        if token in command:
            return f"Error: command contains disallowed operator '{token}'. Use simple commands only."
    cwd = HOME
    if workdir:
        resolved_wd = str(Path(workdir.replace("~", HOME, 1)).expanduser())
        if not _path_allowed(resolved_wd):
            return "Error: workdir not in allowed paths."
        if not os.path.isdir(resolved_wd):
            return "Error: workdir does not exist or is not a directory."
        cwd = resolved_wd
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        output = result.stdout + result.stderr
        return output.strip() if output.strip() else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: command timed out after 30 seconds."
    except Exception as e:
        return f"Error: {e}"


_MAX_READ_BYTES  = 512 * 1024   # 512 KB
_MAX_WRITE_BYTES = 1 * 1024 * 1024  # 1 MB

def run_read_file(path: str) -> str:
    resolved = path.replace("~", HOME)
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
    resolved = path.replace("~", HOME)
    if not _path_allowed(resolved):
        return f"Error: path not allowed: {resolved}"
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        return f"Error: content too large (max {_MAX_WRITE_BYTES} bytes)."
    try:
        p = Path(resolved)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Written: {resolved}"
    except Exception as e:
        return f"Error: {e}"


# ─────────────────────────────────────────────
# EXPLICIT EXPORTS
# ─────────────────────────────────────────────

__all__ = ["TOOL_DEFINITIONS", "run_tool"]


# ─────────────────────────────────────────────
# DISPATCH
# ─────────────────────────────────────────────

def run_tool(name: str, inputs: dict) -> str:
    if name == "shell":
        return run_shell(inputs.get("command", ""), inputs.get("workdir"))
    if name == "read_file":
        return run_read_file(inputs.get("path", ""))
    if name == "write_file":
        return run_write_file(inputs.get("path", ""), inputs.get("content", ""))
    return f"Error: unknown tool '{name}'"
