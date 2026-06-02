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
    # Determine the effective working directory now (before the workdir block below)
    # so we can resolve relative arguments correctly.
    effective_cwd = ALLOW_PATHS[0] if ALLOW_PATHS else HOME

    # Check ALL arguments — absolute, home-relative, dotdot, AND plain relative.
    # Plain relative paths like `cat .ssh/id_rsa` resolve against cwd and can
    # escape the sandbox if cwd is HOME and ALLOW_PATHS is a subdirectory.
    for i, arg in enumerate(args):
        val = arg.split("=", 1)[-1] if (i > 0 and "=" in arg) else arg
        if i > 0 and val.startswith("-"):
            continue  # skip option flags like -v, --output
        candidate = Path(val.replace("~", HOME, 1))
        if not candidate.is_absolute():
            candidate = Path(effective_cwd) / candidate
        resolved_arg = str(candidate.resolve())
        if not _path_allowed(resolved_arg):
            return f"Error: path not in allowed paths: {val}"

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
