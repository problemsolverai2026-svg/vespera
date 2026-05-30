"""
Vespera API Server
------------------
Small Flask API that the Lovable frontend talks to.
Exposes endpoints for:
  - GET  /api/status          — memory stats + component status
  - GET  /api/components      — component list with descriptions and current model config
  - POST /api/components/:name — update a component's model/api key
  - GET  /api/memories        — list memories by layer
  - GET  /api/conversations   — recent conversation history
  - POST /api/chat            — send a message, get a response
  - POST /api/prune/run       — trigger a manual pruning pass
  - POST /api/cleanup/run     — trigger a manual cleanup pass

Run with: python3 api.py
Default port: 5050
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import json
from pathlib import Path
from config import COMPONENTS, get_component, COMPLEXITY_THRESHOLD, PRUNING_INTERVAL_DAYS
from memory.store import (
    init_db, get_memories, get_recent_conversations,
    get_stats, add_conversation,
)
from security import check_api_token, get_status as security_status

app = Flask(__name__)
CORS(app, origins=["http://localhost:3055", "http://127.0.0.1:3055", "http://localhost:5173", "http://127.0.0.1:5173"])

init_db()


def require_auth():
    """Returns error response if token required and missing/wrong. Returns None if OK."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if not check_api_token(token):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return None


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────

@app.route("/api/status")
def status():
    auth_err = require_auth()
    if auth_err: return auth_err
    stats = get_stats()
    return jsonify({
        "ok": True,
        "memory": stats,
        "settings": {
            "complexity_threshold": COMPLEXITY_THRESHOLD,
            "pruning_interval_days": PRUNING_INTERVAL_DAYS,
        }
    })


# ─────────────────────────────────────────────
# COMPONENTS
# ─────────────────────────────────────────────

@app.route("/api/components")
def list_components():
    auth_err = require_auth()
    if auth_err: return auth_err
    """Return all components with their descriptions and current config."""
    safe = {}
    for name, cfg in COMPONENTS.items():
        safe[name] = {
            "name": name,
            "description": cfg.get("description", ""),
            "role": cfg.get("role", ""),
            "model": cfg.get("ollama_model") or cfg.get("model", ""),
            "provider": cfg.get("provider", "ollama"),
            "has_api_key": bool(cfg.get("api_key", "")),
        }
    return jsonify(safe)


@app.route("/api/components/<name>", methods=["POST"])
def update_component(name):
    """Update a component's model or API key. Writes to .env file."""
    if name not in COMPONENTS:
        return jsonify({"ok": False, "error": f"Unknown component: {name}"}), 404

    data = request.json or {}
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    # Read existing .env
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            env_lines = f.readlines()

    def set_env(key, value):
        """Update or append a key in .env lines."""
        for i, line in enumerate(env_lines):
            if line.startswith(f"{key}="):
                env_lines[i] = f"{key}={value}\n"
                return
        env_lines.append(f"{key}={value}\n")

    prefix = name.upper()
    updated = []

    if "model" in data:
        key = f"{prefix}_OLLAMA_MODEL" if name != "cloud" else "CLOUD_MODEL"
        set_env(key, data["model"])
        updated.append("model")

    if "api_key" in data:
        key = f"{prefix}_API_KEY" if name != "cloud" else "CLOUD_API_KEY"
        set_env(key, data["api_key"])
        updated.append("api_key")

    if "provider" in data and name == "cloud":
        set_env("CLOUD_PROVIDER", data["provider"])
        updated.append("provider")

    with open(env_path, "w") as f:
        f.writelines(env_lines)

    return jsonify({"ok": True, "updated": updated, "note": "Restart Vespera to apply changes."})


# ─────────────────────────────────────────────
# MEMORIES
# ─────────────────────────────────────────────

@app.route("/api/memories")
def list_memories():
    auth_err = require_auth()
    if auth_err: return auth_err
    layer = request.args.get("layer")
    limit = int(request.args.get("limit", 20))
    memories = get_memories(layer=layer, limit=limit)
    return jsonify(memories)


@app.route("/api/conversations")
def list_conversations():
    auth_err = require_auth()
    if auth_err: return auth_err
    limit = int(request.args.get("limit", 20))
    convs = get_recent_conversations(limit=limit)
    return jsonify(convs)


# ─────────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────────

@app.route("/api/security")
def get_security():
    auth_err = require_auth()
    if auth_err: return auth_err
    return jsonify({"ok": True, **security_status()})


@app.route("/api/chat", methods=["POST"])
def chat():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "No message provided"}), 400

    from handoff import handle_message
    add_conversation(role="user", content=message)
    result = handle_message(message)
    add_conversation(role="assistant", content=result["response"])

    # Generate TTS if requested
    tts_path = None
    if data.get("tts", False):
        from tts import speak
        tts_path = speak(result["response"])

    return jsonify({
        "ok": True,
        "response": result["response"],
        "handled_by": result["handled_by"],
        "complexity": result["complexity"],
        "audio": tts_path,
    })


# ─────────────────────────────────────────────
# MANUAL TRIGGERS
# ─────────────────────────────────────────────

@app.route("/api/models")
def get_models():
    auth_err = require_auth()
    if auth_err: return auth_err
    """List all locally downloaded Ollama models."""
    import subprocess
    try:
        ollama_bin = os.getenv("OLLAMA_BIN", "/usr/local/bin/ollama")
        if not os.path.exists(ollama_bin):
            import shutil
            ollama_bin = shutil.which("ollama") or ollama_bin
        result = subprocess.run([ollama_bin, "list"], capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        models = []
        for line in lines:
            parts = line.split()
            if parts:
                models.append({"name": parts[0], "size": parts[2] + " " + parts[3] if len(parts) > 3 else ""})
        return jsonify({"ok": True, "models": models})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/reminders", methods=["GET"])
def get_reminders():
    auth_err = require_auth()
    if auth_err: return auth_err
    from scheduler import list_reminders
    return jsonify({"ok": True, "reminders": list_reminders()})


@app.route("/api/reminders", methods=["POST"])
def set_reminder():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    data = request.json or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400
    from scheduler import parse_reminder, add_reminder
    parsed = parse_reminder(text)
    if not parsed:
        return jsonify({"ok": False, "error": "Could not parse reminder"}), 400
    rid = add_reminder(parsed["message"], parsed["fire_at"], parsed.get("recur"))
    return jsonify({"ok": True, "id": rid, "message": parsed["message"], "fire_at": parsed["fire_at"].isoformat()})


@app.route("/api/reminders/<rid>", methods=["DELETE"])
def delete_reminder(rid):
    from scheduler import cancel_reminder
    ok = cancel_reminder(rid)
    return jsonify({"ok": ok})


@app.route("/api/cleanup/run", methods=["POST"])
def run_cleanup():
    from cleanup_crew import run_cleanup as _run
    _run()
    return jsonify({"ok": True, "stats": get_stats()})


@app.route("/api/prune/run", methods=["POST"])
def run_pruning():
    from periodic_pruning import run_pruning as _run
    _run()
    return jsonify({"ok": True, "stats": get_stats()})


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    import signal
    import atexit

    # ── PID lock: ensure only one instance runs at a time ──────────────
    pid_file = Path(__file__).parent / ".api.pid"

    def _check_pid(pid: int) -> bool:
        """Return True if a process with this PID is currently running."""
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False

    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip())
            if _check_pid(existing_pid):
                print(f"[Vespera API] Already running (PID {existing_pid}). Exiting.")
                raise SystemExit(0)
        except ValueError:
            pass  # corrupted pid file — overwrite it

    pid_file.write_text(str(os.getpid()))

    def _remove_pid():
        try:
            pid_file.unlink()
        except FileNotFoundError:
            pass

    atexit.register(_remove_pid)
    def _handle_sigterm(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    # ───────────────────────────────────────────────────────────────────

    import socket

    def find_free_port(start: int, max_tries: int = 10) -> int:
        for p in range(start, start + max_tries):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("", p))
                    return p
                except OSError:
                    continue
        raise RuntimeError(f"No free port found starting at {start}")

    base_port = int(os.getenv("API_PORT", "5055"))
    port = find_free_port(base_port)
    if port != base_port:
        print(f"[Vespera API] Port {base_port} in use — using {port} instead.")
        print(f"[Vespera API] Tip: set API_PORT={port} in your .env to make this permanent.")

    # Write actual port to a file so other components can find it
    (Path(__file__).parent / ".port").write_text(str(port))

    print(f"[Vespera API] Running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
