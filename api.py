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
Default port: 5055
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import json
import threading
from pathlib import Path
from config import COMPONENTS, get_component, COMPLEXITY_THRESHOLD, PRUNING_INTERVAL_DAYS
from memory.store import (
    init_db, get_memories, get_recent_conversations,
    get_stats, add_conversation, backup_db,
)
from security import check_api_token, get_status as security_status

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB request body limit
_env_lock     = threading.Lock()
_cleanup_lock = threading.Lock()
_pruning_lock = threading.Lock()

# ── Rate limiter for /api/chat ─────────────────────────────────────────────
# Allows at most RATE_LIMIT_MAX_CALLS calls within RATE_LIMIT_WINDOW_SECONDS.
import time as _time
_rate_lock     = threading.Lock()
_rate_calls: dict[str, list] = {}   # keyed by remote IP
RATE_LIMIT_MAX_CALLS      = int(os.getenv("CHAT_RATE_LIMIT", "30"))
RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_DICT_MAX_IPS        = 10_000  # cap dict size to prevent unbounded growth

def _check_rate_limit(remote_addr: str) -> bool:
    """Return True if the request is allowed, False if rate-limited (per IP)."""
    now = _time.time()
    with _rate_lock:
        # Evict oldest IP if dict is growing too large
        if len(_rate_calls) >= _RATE_DICT_MAX_IPS and remote_addr not in _rate_calls:
            oldest_ip = next(iter(_rate_calls))
            del _rate_calls[oldest_ip]
        calls = _rate_calls.setdefault(remote_addr, [])
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        while calls and calls[0] < cutoff:
            calls.pop(0)
        if len(calls) >= RATE_LIMIT_MAX_CALLS:
            return False
        calls.append(now)
        return True

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"ok": False, "error": "Request body too large (max 1 MB)"}), 413
# Build CORS origins dynamically from configured ports
_ui_port = os.getenv("UI_PORT", "3055")
CORS(app, origins=[
    f"http://localhost:{_ui_port}",
    f"http://127.0.0.1:{_ui_port}",
    "http://localhost:5173",   # Vite default dev port
    "http://127.0.0.1:5173",
])

init_db()


def require_auth():
    """Returns error response if token required and missing/wrong. Returns None if OK."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else auth_header.strip()
    if not check_api_token(token):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    return None


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────

@app.route("/health")
def health():
    """Lightweight liveness check for start.sh readiness polling."""
    return jsonify({"ok": True})


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
    """Return all components with their descriptions and current config."""
    auth_err = require_auth()
    if auth_err: return auth_err
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
    auth_err = require_auth()
    if auth_err: return auth_err
    if name not in COMPONENTS:
        return jsonify({"ok": False, "error": f"Unknown component: {name}"}), 404

    data = request.json or {}

    # Sanitize values — strip newlines to prevent env injection
    def _safe_value(v: str) -> str:
        # Strip newlines (env injection). Escape \ before " so the quoted
        # write format KEY="value" is never broken by a literal \ or ".
        return (
            str(v)
            .replace("\n", "")
            .replace("\r", "")
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .strip()
        )

    env_path = os.path.join(os.path.dirname(__file__), ".env")

    with _env_lock:
        # Read existing .env
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path) as f:
                env_lines = f.readlines()

        def set_env(key, value):
            """Update or append a key in .env lines.
            Values are double-quoted so characters like '#' are preserved
            correctly by python-dotenv (unquoted '#' starts a comment).
            """
            line = f'{key}="{value}"\n'
            for i, existing in enumerate(env_lines):
                if existing.startswith(f"{key}="):
                    env_lines[i] = line
                    return
            env_lines.append(line)

        # Map component name to the env var prefix config.py actually reads
        _ENV_PREFIX = {
            "background_loop": "BACKGROUND",
            "cleanup_crew":    "CLEANUP",
            "periodic_pruning": "PRUNING",
            "handoff":         "HANDOFF",
            "cloud":           "CLOUD",
        }
        prefix = _ENV_PREFIX.get(name, name.upper())
        updated = []

        if "model" in data:
            key = f"{prefix}_MODEL" if name == "cloud" else f"{prefix}_OLLAMA_MODEL"
            set_env(key, _safe_value(data["model"]))
            updated.append("model")

        if "api_key" in data:
            set_env(f"{prefix}_API_KEY", _safe_value(data["api_key"]))
            updated.append("api_key")

        if "provider" in data and name == "cloud":
            set_env("CLOUD_PROVIDER", _safe_value(data["provider"]))
            updated.append("provider")

        # Atomic write — write to temp file then rename so a crash can't corrupt .env
        tmp_path = env_path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                f.writelines(env_lines)
            os.replace(tmp_path, env_path)
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            return jsonify({"ok": False, "error": f"Failed to write config: {e}"}), 500

    return jsonify({"ok": True, "updated": updated, "note": "Restart Vespera to apply changes."})


# ─────────────────────────────────────────────
# MEMORIES
# ─────────────────────────────────────────────

@app.route("/api/memories")
def list_memories():
    auth_err = require_auth()
    if auth_err: return auth_err
    layer = request.args.get("layer")
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 1000))
    except (ValueError, TypeError):
        limit = 20
    memories = get_memories(layer=layer, limit=limit)
    return jsonify(memories)


@app.route("/api/conversations")
def list_conversations():
    auth_err = require_auth()
    if auth_err: return auth_err
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 1000))
    except (ValueError, TypeError):
        limit = 20
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
    if not _check_rate_limit(request.remote_addr or "unknown"):
        return jsonify({"ok": False, "error": f"Rate limit exceeded ({RATE_LIMIT_MAX_CALLS} requests/minute)"}), 429
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "No message provided"}), 400
    if len(message) > 8000:
        return jsonify({"ok": False, "error": "Message too long (max 8000 chars)"}), 400

    from handoff import handle_message
    add_conversation(role="user", content=message)
    try:
        result = handle_message(message)
    except Exception as e:
        import traceback
        app.logger.error("chat() exception: %s", traceback.format_exc())
        add_conversation(role="assistant", content="[Internal error]")
        return jsonify({"ok": False, "error": "Internal server error"}), 500
    add_conversation(role="assistant", content=result.get("response", ""))

    response_text = result.get("response", "")

    # Generate TTS if requested
    tts_path = None
    tts_url  = None
    if data.get("tts", False):
        from tts import speak
        tts_path = speak(response_text)
        if tts_path:
            tts_url = f"/api/audio/{os.path.basename(tts_path)}"

    return jsonify({
        "ok": True,
        "response": response_text,
        "handled_by": result.get("handled_by", "unknown"),
        "complexity": result.get("complexity", 0.0),
        "audio": tts_url,
    })


# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────

# Settings that users can change through the UI.
# Each entry: env_var, label, description, type, default
_SETTINGS_SCHEMA = [
    {
        "key":         "CHAT_RATE_LIMIT",
        "label":       "Chat Rate Limit",
        "description": "Maximum number of /api/chat requests allowed per minute. Default: 30.",
        "type":        "number",
        "default":     30,
    },
    {
        "key":         "VESPERA_MAX_TOKENS",
        "label":       "Max Cloud AI Tokens",
        "description": "Maximum tokens per cloud AI response (cost control). Default: 1024.",
        "type":        "number",
        "default":     1024,
    },
    {
        "key":         "COMPLEXITY_THRESHOLD",
        "label":       "Cloud AI Complexity Threshold",
        "description": "Score (0.0–1.0) at which messages are sent to the cloud AI. Lower = more cloud usage. Default: 0.65.",
        "type":        "float",
        "default":     0.65,
    },
    {
        "key":         "BACKGROUND_LOOP_INTERVAL",
        "label":       "Background Loop Interval (seconds)",
        "description": "How often the background thinking loop runs. Default: 180 seconds.",
        "type":        "number",
        "default":     180,
    },
]


@app.route("/api/settings", methods=["GET"])
def get_settings():
    """Return current values for all user-facing settings."""
    auth_err = require_auth()
    if auth_err: return auth_err
    result = []
    for s in _SETTINGS_SCHEMA:
        raw = os.getenv(s["key"])
        if raw is not None:
            try:
                value = float(raw) if s["type"] == "float" else int(raw)
            except ValueError:
                value = s["default"]
        else:
            value = s["default"]
        result.append({**s, "value": value})
    return jsonify({"ok": True, "settings": result})


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """Update one or more settings. Writes to .env. Restart required to apply."""
    auth_err = require_auth()
    if auth_err: return auth_err

    data = request.json or {}
    valid_keys = {s["key"] for s in _SETTINGS_SCHEMA}
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    with _env_lock:
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path) as f:
                env_lines = f.readlines()

        def set_env(key, value):
            safe = _safe_value(str(value))
            line = f'{key}="{safe}"\n'
            for i, existing in enumerate(env_lines):
                if existing.startswith(f"{key}="):
                    env_lines[i] = line
                    return
            env_lines.append(line)

        # Interval keys must be >= 1 to avoid tight loops
        _INTERVAL_KEYS = {"BACKGROUND_LOOP_INTERVAL", "CHAT_RATE_LIMIT", "VESPERA_MAX_TOKENS"}

        updated = []
        for key, value in data.items():
            if key not in valid_keys:
                return jsonify({"ok": False, "error": f"Unknown setting: {key}"}), 400
            # Validate type
            try:
                schema = next(s for s in _SETTINGS_SCHEMA if s["key"] == key)
                if schema["type"] in ("number", "float"):
                    value = float(value) if schema["type"] == "float" else int(value)
                    min_val = 1 if key in _INTERVAL_KEYS else 0
                    if value < min_val:
                        return jsonify({"ok": False, "error": f"{key} must be >= {min_val}"}), 400
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": f"Invalid value for {key}"}), 400
            set_env(key, value)
            updated.append(key)

        tmp_path = env_path + ".tmp"
        try:
            with open(tmp_path, "w") as f:
                f.writelines(env_lines)
            os.replace(tmp_path, env_path)
        except Exception as e:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            return jsonify({"ok": False, "error": f"Failed to write config: {e}"}), 500

    return jsonify({"ok": True, "updated": updated, "note": "Restart Vespera to apply changes."})


# ─────────────────────────────────────────────
# MANUAL TRIGGERS
# ─────────────────────────────────────────────

@app.route("/api/models")
def get_models():
    """List all locally downloaded Ollama models."""
    auth_err = require_auth()
    if auth_err: return auth_err
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
            if not parts:
                continue
            try:
                name = parts[0]
                # Ollama list format: NAME  ID  SIZE  MODIFIED
                # Size is usually like "4.7 GB" (2 tokens) but format can vary
                size = " ".join(parts[2:4]) if len(parts) >= 4 else (parts[2] if len(parts) >= 3 else "")
                models.append({"name": name, "size": size})
            except Exception:
                continue
        return jsonify({"ok": True, "models": models})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/audio/<filename>")
def serve_audio(filename):
    """Serve a TTS audio file by name."""
    auth_err = require_auth()
    if auth_err: return auth_err
    from flask import send_from_directory
    import re
    # Only allow safe filenames (hex + extension)
    if not re.match(r'^[a-f0-9]+\.(mp3|wav)$', filename):
        return jsonify({"ok": False, "error": "Invalid filename"}), 400
    from pathlib import Path as _Path
    tts_dir = str(_Path.home() / ".vespera" / "tts")
    return send_from_directory(tts_dir, filename)


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
    auth_err = require_auth()
    if auth_err: return auth_err
    from scheduler import cancel_reminder
    ok = cancel_reminder(rid)
    return jsonify({"ok": ok})


@app.route("/api/backup", methods=["POST"])
def run_backup():
    auth_err = require_auth()
    if auth_err: return auth_err
    from datetime import datetime
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(os.path.dirname(__file__), "backups", f"vespera_{ts}.db")
    try:
        path = backup_db(dest)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "backup": path})


@app.route("/api/cleanup/run", methods=["POST"])
def run_cleanup():
    """Run memory cleanup manually."""
    auth_err = require_auth()
    if auth_err: return auth_err
    acquired = _cleanup_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"ok": False, "error": "Cleanup already running"}), 409

    def _run_and_release():
        try:
            from cleanup_crew import run_cleanup as _run
            _run()
        finally:
            _cleanup_lock.release()

    threading.Thread(target=_run_and_release, daemon=True, name="manual-cleanup").start()
    return jsonify({"ok": True, "status": "started"}), 202


@app.route("/api/prune/run", methods=["POST"])
def run_pruning():
    """Run memory pruning manually."""
    auth_err = require_auth()
    if auth_err: return auth_err
    acquired = _pruning_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"ok": False, "error": "Pruning already running"}), 409

    def _run_and_release():
        try:
            from periodic_pruning import run_pruning as _run
            _run()
        finally:
            _pruning_lock.release()

    threading.Thread(target=_run_and_release, daemon=True, name="manual-prune").start()
    return jsonify({"ok": True, "status": "started"}), 202


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import socket
    import signal
    import subprocess
    import shutil
    import fcntl

    # ── PID lock: ensure only one instance runs at a time ──────────────
    lock_file = Path(__file__).parent / ".api.lock"
    _lockfd = open(lock_file, 'w')
    try:
        fcntl.flock(_lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("[Vespera API] Already running. Exiting.")
        raise SystemExit(0)
    _lockfd.write(str(os.getpid()))
    _lockfd.flush()
    # flock released automatically by OS on exit (including SIGKILL)

    def _handle_sigterm(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    # ───────────────────────────────────────────────────────────────────

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

    # Write actual port so start.sh / telegram_bot.py can find it
    (Path(__file__).parent / ".port").write_text(str(port))

    bind_host = os.getenv("VESPERA_BIND_HOST", "127.0.0.1")

    # Threading locks (_cleanup_lock, _pruning_lock, _env_lock) are in-process only.
    # Multi-worker gunicorn would share no lock state across workers — clamp to 1.
    try:
        _requested_workers = int(os.getenv("VESPERA_WORKERS", "1"))
    except ValueError:
        print("[Vespera API] WARNING: VESPERA_WORKERS is not a valid integer — using 1.")
        _requested_workers = 1
    if _requested_workers != 1:
        print(f"[Vespera API] WARNING: VESPERA_WORKERS={_requested_workers} ignored — must be 1 (in-process locks are not multi-worker safe). Using 1.")
    workers = 1

    # Locate gunicorn: system PATH first, then local venv (for users who ran setup.sh)
    gunicorn_bin = (
        shutil.which("gunicorn")
        or (str(Path(__file__).parent / "venv" / "bin" / "gunicorn")
            if (Path(__file__).parent / "venv" / "bin" / "gunicorn").is_file() else None)
    )

    if gunicorn_bin:
        print(f"[Vespera API] Starting via gunicorn on http://{bind_host}:{port}")
        # Clear FD_CLOEXEC so gunicorn's master process inherits the lock fd
        # and holds it for its lifetime — prevents a second invocation spawning
        # a duplicate gunicorn on port+1.
        import fcntl as _fcntl2
        flags = _fcntl2.fcntl(_lockfd.fileno(), _fcntl2.F_GETFD)
        _fcntl2.fcntl(_lockfd.fileno(), _fcntl2.F_SETFD, flags & ~_fcntl2.FD_CLOEXEC)
        os.execv(gunicorn_bin, [
            gunicorn_bin,
            f"--bind={bind_host}:{port}",
            f"--workers={workers}",
            "--timeout=120",
            "--access-logfile=-",
            "api:app",
        ])
    else:
        print(f"[Vespera API] gunicorn not found — falling back to Werkzeug dev server.")
        print(f"[Vespera API] Install it: pip install gunicorn")
        print(f"[Vespera API] Running on http://{bind_host}:{port}")
        app.run(host=bind_host, port=port, debug=False)
