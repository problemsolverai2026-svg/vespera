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
import threading
from pathlib import Path
from config import COMPONENTS, COMPLEXITY_THRESHOLD, PRUNING_INTERVAL_DAYS
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
_backup_lock  = threading.Lock()

# ── Rate limiter for /api/chat ─────────────────────────────────────────────
# Allows at most RATE_LIMIT_MAX_CALLS calls within RATE_LIMIT_WINDOW_SECONDS.
import time as _time
import math as _math
from collections import deque as _deque
_rate_lock     = threading.Lock()
_rate_calls: dict[str, _deque] = {}   # keyed by remote IP
try:
    RATE_LIMIT_MAX_CALLS = max(1, int(os.getenv("CHAT_RATE_LIMIT", "30")))
except (ValueError, TypeError):
    RATE_LIMIT_MAX_CALLS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_DICT_MAX_IPS        = 10_000  # cap dict size to prevent unbounded growth

def _check_rate_limit(remote_addr: str) -> bool:
    """Return True if the request is allowed, False if rate-limited (per IP)."""
    now = _time.time()
    with _rate_lock:
        # Evict oldest IP if dict is growing too large
        if len(_rate_calls) >= _RATE_DICT_MAX_IPS and remote_addr not in _rate_calls:
            # Evict the IP with the oldest last-seen timestamp (LRU)
            oldest_ip = min(_rate_calls, key=lambda ip: _rate_calls[ip][-1] if _rate_calls[ip] else 0)
            del _rate_calls[oldest_ip]
        calls = _rate_calls.setdefault(remote_addr, _deque())
        cutoff = now - RATE_LIMIT_WINDOW_SECONDS
        while calls and calls[0] < cutoff:
            calls.popleft()
        if len(calls) >= RATE_LIMIT_MAX_CALLS:
            return False
        calls.append(now)
        return True

@app.errorhandler(413)
def request_too_large(e):
    return jsonify({"ok": False, "error": "Request body too large (max 1 MB)"}), 413
# Build CORS origins dynamically from configured ports
try:
    _ui_port = str(int(os.getenv("UI_PORT", "3055")))  # validate numeric
except (ValueError, TypeError):
    _ui_port = "3055"
_cors_origins = [
    f"http://localhost:{_ui_port}",
    f"http://127.0.0.1:{_ui_port}",
]
if os.getenv("VESPERA_DEV", "false").lower() == "true":
    # Dev-only: allow Vite's default port (set VESPERA_DEV=true in .env for local UI dev)
    _cors_origins += ["http://localhost:5173", "http://127.0.0.1:5173"]
    import logging as _logging
    _logging.getLogger("vespera.api").warning(
        "VESPERA_DEV=true — CORS is widened to include localhost:5173. "
        "Do NOT use this setting in production."
    )
CORS(app, origins=_cors_origins)

# init_db() is called once by main.py at startup — not here at import time


def _safe_env_value(v: str, max_len: int = 2048) -> str:
    """Sanitize a value before writing it into .env.
    Strips characters that would cause injection if the file is ever bash-sourced.
    """
    s = str(v)[:max_len]          # length cap
    return (
        s
        .replace("\n", "")
        .replace("\r", "")
        .replace("\x00", "")     # null bytes can truncate .env parsing
        .replace("\\", "\\\\")
        # NOTE: do NOT escape $ — python-dotenv does not expand variables,
        # so escaping $ to \$ causes it to be read back as literal \$,
        # which breaks any API key that legitimately contains a $ character.
        .replace("`",  "")        # strip backticks — no safe escape in double-quoted values
        .replace('"', '\\"')
        .strip()
    )


_TRUST_PROXY = os.getenv("TRUST_PROXY", "false").lower() == "true"

def _get_client_ip() -> str:
    """Return the real client IP, honouring X-Forwarded-For only when TRUST_PROXY=true."""
    if _TRUST_PROXY:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # Use rightmost IP — appended by our proxy, not spoofable by client
            return forwarded.split(",")[-1].strip()
    return request.remote_addr or "unknown"


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
    # Read live from env so /api/status and /api/settings report the same value
    try:
        _ct = float(os.getenv("COMPLEXITY_THRESHOLD", str(COMPLEXITY_THRESHOLD)))
    except (ValueError, TypeError):
        _ct = COMPLEXITY_THRESHOLD
    return jsonify({
        "ok": True,
        "memory": stats,
        "settings": {
            "complexity_threshold": _ct,
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
    # Re-read from env so has_api_key reflects UI saves without restart
    try:
        from dotenv import load_dotenv as _ldenv
        _ldenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)
    except ImportError:
        pass
    _ENV_PREFIX = {
        "background_loop": "BACKGROUND",
        "cleanup_crew":    "CLEANUP",
        "periodic_pruning": "PRUNING",
        "handoff":         "HANDOFF",
        "cloud":           "CLOUD",
    }
    safe = {}
    for name, cfg in COMPONENTS.items():
        prefix = _ENV_PREFIX.get(name, name.upper())
        live_key = os.getenv(f"{prefix}_API_KEY", cfg.get("api_key", ""))
        safe[name] = {
            "name": name,
            "description": cfg.get("description", ""),
            "role": cfg.get("role", ""),
            "model": cfg.get("ollama_model") or cfg.get("model", ""),
            "provider": cfg.get("provider", "ollama"),
            "has_api_key": bool(live_key),
        }
    return jsonify({"ok": True, "components": safe})


@app.route("/api/components/<name>", methods=["POST"])
def update_component(name):
    """Update a component's model or API key. Writes to .env file."""
    auth_err = require_auth()
    if auth_err: return auth_err
    if name not in COMPONENTS:
        return jsonify({"ok": False, "error": "Not found"}), 404

    data = request.json or {}
    env_path = os.path.join(os.path.dirname(__file__), ".env")

    with _env_lock:
        env_lines = []
        if os.path.exists(env_path):
            with open(env_path) as f:
                env_lines = f.readlines()

        def set_env(key, value):
            """Update or append a key in .env lines."""
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
        if name not in _ENV_PREFIX:
            app.logger.warning(
                "update_component: no _ENV_PREFIX entry for component '%s' — "
                "falling back to %s. Add it to _ENV_PREFIX to suppress this warning.",
                name, name.upper()
            )
        prefix = _ENV_PREFIX.get(name, name.upper())
        updated = []

        if "model" in data:
            key = f"{prefix}_MODEL" if name == "cloud" else f"{prefix}_OLLAMA_MODEL"
            set_env(key, _safe_env_value(data["model"]))
            updated.append("model")

        if "api_key" in data:
            set_env(f"{prefix}_API_KEY", _safe_env_value(data["api_key"]))
            updated.append("api_key")

        if "provider" in data and name == "cloud":
            set_env("CLOUD_PROVIDER", _safe_env_value(data["provider"]))
            updated.append("provider")

        # Atomic write (0o600) — secrets must not be world-readable
        tmp_path = env_path + ".tmp"
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.writelines(env_lines)
            os.replace(tmp_path, env_path)
        except Exception as e:
            app.logger.error("Failed to write .env: %s", e)
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            return jsonify({"ok": False, "error": "Failed to write config"}), 500

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
    return jsonify({"ok": True, "memories": memories})


@app.route("/api/conversations")
def list_conversations():
    auth_err = require_auth()
    if auth_err: return auth_err
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 1000))
    except (ValueError, TypeError):
        limit = 20
    convs = get_recent_conversations(limit=limit)
    return jsonify({"ok": True, "conversations": convs})


# ─────────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────────

@app.route("/api/security")
def get_security():
    auth_err = require_auth()
    if auth_err: return auth_err
    status = security_status()
    # Append the actual user ID list here — behind auth, never in get_status() directly.
    # Re-read from env so the UI reflects saves without restart.
    try:
        from dotenv import load_dotenv as _ldenv
        _ldenv(os.path.join(os.path.dirname(__file__), ".env"), override=True)
    except ImportError:
        pass
    status["telegram_allowed_users"] = [
        u.strip() for u in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if u.strip()
    ]
    return jsonify({"ok": True, **status})


@app.route("/api/chat", methods=["POST"])
def chat():
    auth_err = require_auth()
    if auth_err:
        return auth_err
    if not _check_rate_limit(_get_client_ip()):
        resp = jsonify({"ok": False, "error": "Too many requests"})
        resp.headers["Retry-After"] = str(RATE_LIMIT_WINDOW_SECONDS)
        return resp, 429
    data = request.json or {}
    message = str(data.get("message", "")).strip()
    if not message:
        return jsonify({"ok": False, "error": "No message provided"}), 400
    if len(message) > 8000:
        return jsonify({"ok": False, "error": "Message too long (max 8000 chars)"}), 400

    from handoff import handle_message
    from utils import _sanitize
    safe_message = _sanitize(message, 8000)
    if not safe_message:
        return jsonify({"ok": False, "error": "Message contained only invalid characters"}), 400
    add_conversation(role="user", content=safe_message)

    # Fire-and-forget fact extraction — stores durable user facts directly to
    # validated memory without going through the background loop thought pipeline.
    from facts import extract_facts_async
    extract_facts_async(safe_message)

    try:
        result = handle_message(safe_message)
        if not isinstance(result, dict):
            raise ValueError(f"handle_message returned unexpected type: {type(result)}")
        response_text = result.get("response", "")
        handled_by = result.get("handled_by", "local")
        try:
            complexity_score = float(result.get("complexity", 0.0))
        except (TypeError, ValueError):
            complexity_score = 0.0
        # Sanitize model output before storing — prevents injection-pattern
        # responses from re-entering future prompts via conversation history.
        from utils import _sanitize as _san
        safe_response = _san(response_text, len(response_text)) if response_text else ""
        add_conversation(
            role="assistant",
            content=safe_response,
            used_cloud=("cloud" in handled_by),  # covers "cloud" and "search+cloud"
            complexity=complexity_score,
        )

        # Generate TTS if requested — non-critical; never fail the response over it
        tts_url = None
        if data.get("tts", False):
            try:
                from tts import speak
                tts_path = speak(safe_response)  # use sanitized version
                if tts_path:
                    tts_url = f"/api/audio/{os.path.basename(tts_path)}"
            except Exception:
                app.logger.exception("TTS failed; returning response without audio")

        return jsonify({
            "ok": True,
            "response": safe_response,  # use sanitized version — same as what's stored in DB
            "handled_by": result.get("handled_by", "unknown"),
            "complexity": result.get("complexity", 0.0),
            "audio": tts_url,
        })
    except Exception:
        import traceback
        app.logger.error("chat() exception: %s", traceback.format_exc())
        try:
            add_conversation(role="assistant", content="[Internal error]")
        except Exception:
            app.logger.exception("Failed to log error conversation to DB")
        return jsonify({"ok": False, "error": "Internal server error"}), 500


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
    {
        "key":         "TELEGRAM_ALLOWED_USERS",
        "label":       "Telegram Allowed User IDs",
        "description": "Comma-separated Telegram user IDs allowed to use the bot. Empty = unrestricted.",
        "type":        "string",
        "default":     "",
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
            if s["type"] == "string":
                value = raw  # return as-is — no numeric cast
            else:
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
            safe = _safe_env_value(str(value))
            line = f'{key}="{safe}"\n'
            for i, existing in enumerate(env_lines):
                if existing.startswith(f"{key}="):
                    env_lines[i] = line
                    return
            env_lines.append(line)

        # Per-key bounds: (min, max). None = no bound on that side.
        _KEY_BOUNDS = {
            "BACKGROUND_LOOP_INTERVAL": (1,   86400),   # 1s – 24h
            "CHAT_RATE_LIMIT":          (1,   1000),    # 1 – 1000 req/min
            "VESPERA_MAX_TOKENS":       (1,   32768),   # 1 – 32k tokens
            "COMPLEXITY_THRESHOLD":     (0.0, 1.0),     # 0.0 – 1.0
        }
        updated = []
        for key, value in data.items():
            if key not in valid_keys:
                return jsonify({"ok": False, "error": f"Unknown setting: {key}"}), 400
            # Validate type
            try:
                schema = next(s for s in _SETTINGS_SCHEMA if s["key"] == key)
                if schema["type"] in ("number", "float"):
                    value = float(value) if schema["type"] == "float" else int(value)
                    if not _math.isfinite(value):
                        return jsonify({"ok": False, "error": f"{key} must be a finite number"}), 400
                    bounds = _KEY_BOUNDS.get(key)
                    if bounds:
                        lo, hi = bounds
                        if value < lo or value > hi:
                            return jsonify({"ok": False, "error": f"{key} must be {lo}–{hi}"}), 400
                elif key == "TELEGRAM_ALLOWED_USERS":
                    # Each entry must be a numeric Telegram user ID (or the field is empty)
                    ids = [u.strip() for u in str(value).split(",") if u.strip()]
                    if not all(u.isdigit() for u in ids):
                        return jsonify({"ok": False, "error": "TELEGRAM_ALLOWED_USERS must be comma-separated numeric user IDs (e.g. 123456789)"}), 400
            except (ValueError, TypeError):
                return jsonify({"ok": False, "error": f"Invalid value for {key}"}), 400
            set_env(key, value)
            # Also update in-process env so GET /api/settings reflects the new value immediately.
            # NOTE: RATE_LIMIT_MAX_CALLS is a module-level constant frozen at import time —
            # updating os.environ does NOT change the active rate limit until restart.
            # This is intentional: the rate limiter requires a restart to take effect.
            os.environ[key] = str(value)
            updated.append(key)

        # Atomic write (0o600) — secrets must not be world-readable
        tmp_path = env_path + ".tmp"
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.writelines(env_lines)
            os.replace(tmp_path, env_path)
        except Exception as e:
            app.logger.error("Failed to write .env: %s", e)
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
            return jsonify({"ok": False, "error": "Failed to write config"}), 500

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
        if result.returncode != 0:
            app.logger.error("ollama list failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return jsonify({"ok": False, "error": "Model list unavailable"}), 503
        import re as _re
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        models = []
        for line in lines:
            if not line.strip():
                continue
            # Ollama list: NAME  ID  SIZE  MODIFIED
            # SIZE format varies: "3.2 GB" or "3.2GB" — use regex to capture robustly
            m = _re.match(r'^(\S+)\s+\S+\s+(\S+(?:\s+[KMGT]B)?)\s+', line, _re.IGNORECASE)
            if m:
                models.append({"name": m.group(1), "size": m.group(2).strip()})
            else:
                parts = line.split()
                if parts:
                    models.append({"name": parts[0], "size": ""})
        return jsonify({"ok": True, "models": models})
    except Exception as e:
        app.logger.error("get_models failed: %s", e)
        return jsonify({"ok": False, "error": "Model list unavailable"}), 503


@app.route("/api/audio/<filename>")
def serve_audio(filename):
    """Serve a TTS audio file by name.
    No auth required — filenames are 32-64 hex chars (UUID-based, unguessable).
    Adding auth here would break the webchat player which fetches audio without headers.
    """
    from flask import send_from_directory
    import re
    # Only allow safe filenames (hex + extension) — prevents path traversal
    if not re.match(r'^[a-f0-9]{32,64}\.(mp3|wav)$', filename):
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
    import re
    rid = rid.lower()  # normalize so uppercase UUIDs don't bypass the lowercase-only pattern
    if not re.match(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', rid):
        return jsonify({"ok": False, "error": "Invalid reminder id"}), 400
    from scheduler import cancel_reminder
    ok = cancel_reminder(rid)
    return jsonify({"ok": ok})


# ─────────────────────────────────────────────
# NOTES
# ─────────────────────────────────────────────

@app.route("/notes")
def notes_ui():
    """Simple notes UI page — no auth required for local use."""
    return '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vespera Notes</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 2rem; }
  h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 1.5rem;
       display: flex; align-items: center; gap: .5rem; }
  .add-row { display: flex; gap: .75rem; margin-bottom: 2rem; }
  .add-row input { flex: 1; background: #1e2130; border: 1px solid #2d3248;
                   border-radius: 8px; padding: .65rem 1rem; color: #e2e8f0;
                   font-size: .95rem; outline: none; }
  .add-row input:focus { border-color: #6366f1; }
  .add-row button { background: #6366f1; color: #fff; border: none;
                    border-radius: 8px; padding: .65rem 1.25rem;
                    font-size: .95rem; cursor: pointer; white-space: nowrap; }
  .add-row button:hover { background: #4f46e5; }
  .notes-list { display: flex; flex-direction: column; gap: .75rem; }
  .note-card { background: #1e2130; border: 1px solid #2d3248; border-radius: 10px;
               padding: 1rem 1.25rem; display: flex; justify-content: space-between;
               align-items: flex-start; gap: 1rem; }
  .note-content { flex: 1; font-size: .95rem; line-height: 1.5; word-break: break-word; }
  .note-meta { font-size: .75rem; color: #64748b; margin-top: .3rem; }
  .delete-btn { background: none; border: none; color: #64748b; cursor: pointer;
                font-size: 1.1rem; padding: .2rem .4rem; border-radius: 4px;
                flex-shrink: 0; }
  .delete-btn:hover { color: #f87171; background: #2d1f1f; }
  .empty { text-align: center; color: #64748b; padding: 3rem;
           font-size: .95rem; }
  .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; background: #22c55e;
           color: #fff; padding: .6rem 1rem; border-radius: 8px;
           font-size: .9rem; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: 1; }
</style>
</head>
<body>
<h1>📝 Vespera Notes</h1>
<div class="add-row">
  <input id="input" type="text" placeholder="Type a note and press Enter or Save…" />
  <button onclick="saveNote()">Save</button>
</div>
<div class="notes-list" id="list"></div>
<div class="empty" id="empty" style="display:none">No notes yet. Add one above.</div>
<div class="toast" id="toast"></div>
<script>
  const API = \'\' ;
  async function load() {
    const r = await fetch(API + \'/api/notes\');
    const notes = await r.json();
    const list = document.getElementById(\'list\');
    const empty = document.getElementById(\'empty\');
    list.innerHTML = \'\';
    if (!notes.length) { empty.style.display = \'\'; return; }
    empty.style.display = \'none\';
    notes.forEach(n => {
      const d = new Date(n.created_at);
      const dateStr = d.toLocaleDateString(\'en-US\', {month:\'short\',day:\'numeric\'})
                    + \' \' + d.toLocaleTimeString(\'en-US\',{hour:\'numeric\',minute:\'2-digit\'});
      const card = document.createElement(\'div\');
      card.className = \'note-card\';
      card.innerHTML = `
        <div class="note-content">
          <div>${escHtml(n.content)}</div>
          <div class="note-meta">${dateStr} &nbsp;·&nbsp; ${n.id.slice(0,8)}</div>
        </div>
        <button class="delete-btn" title="Delete" onclick="del(\'${n.id}\')">✕</button>`;
      list.appendChild(card);
    });
  }
  function escHtml(s) {
    return s.replace(/&/g,\'&amp;\').replace(/</g,\'&lt;\').replace(/>/g,\'&gt;\');
  }
  async function saveNote() {
    const inp = document.getElementById(\'input\');
    const content = inp.value.trim();
    if (!content) return;
    await fetch(API + \'/api/notes\', {
      method: \'POST\', headers:{\'Content-Type\':\'application/json\'},
      body: JSON.stringify({content})
    });
    inp.value = \'\';
    toast(\'Note saved ✓\');
    load();
  }
  async function del(id) {
    await fetch(API + \'/api/notes/\' + id, {method:\'DELETE\'});
    toast(\'Deleted\');
    load();
  }
  function toast(msg) {
    const t = document.getElementById(\'toast\');
    t.textContent = msg; t.classList.add(\'show\');
    setTimeout(() => t.classList.remove(\'show\'), 2000);
  }
  document.getElementById(\'input\').addEventListener(\'keydown\', e => {
    if (e.key === \'Enter\') saveNote();
  });
  load();
</script>
</body>
</html>
'''


@app.route("/api/notes", methods=["GET"])
def get_notes():
    auth_err = require_auth()
    if auth_err: return auth_err
    try:
        from notes import list_notes, init_notes_db
        init_notes_db()
        return jsonify(list_notes())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/notes", methods=["POST"])
def create_note():
    auth_err = require_auth()
    if auth_err: return auth_err
    data = request.get_json(silent=True) or {}
    content = str(data.get("content", "")).strip()
    if not content:
        return jsonify({"error": "content is required"}), 400
    try:
        from notes import add_note, init_notes_db
        init_notes_db()
        note = add_note(content)
        return jsonify(note), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/notes/<note_id>", methods=["DELETE"])
def remove_note(note_id):
    auth_err = require_auth()
    if auth_err: return auth_err
    try:
        from notes import delete_note, init_notes_db
        init_notes_db()
        ok = delete_note(note_id)
        if ok:
            return jsonify({"deleted": True})
        return jsonify({"error": "Note not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────
# PHOTOS
# ─────────────────────────────────────────────

@app.route("/photos")
def photos_ui():
    """Simple photo viewer page — for local use via browser."""
    return '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vespera Photos</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 2rem; }
  h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 1.5rem;
       display: flex; align-items: center; gap: .5rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 1rem; }
  .card { background: #1e2130; border: 1px solid #2d3248; border-radius: 10px;
          overflow: hidden; display: flex; flex-direction: column; }
  .card img { width: 100%; aspect-ratio: 4/3; object-fit: cover;
              cursor: pointer; display: block; }
  .card-body { padding: .75rem 1rem; flex: 1; }
  .caption { font-size: .9rem; line-height: 1.4; word-break: break-word;
             color: #e2e8f0; margin-bottom: .4rem; }
  .caption.empty { color: #64748b; font-style: italic; }
  .meta { font-size: .72rem; color: #64748b; }
  .actions { display: flex; justify-content: flex-end; padding: .5rem .75rem;
             border-top: 1px solid #2d3248; }
  .delete-btn { background: none; border: none; color: #64748b; cursor: pointer;
                font-size: .85rem; padding: .2rem .5rem; border-radius: 4px; }
  .delete-btn:hover { color: #f87171; background: #2d1f1f; }
  .empty { text-align: center; color: #64748b; padding: 4rem;
           font-size: .95rem; }
  .toast { position: fixed; bottom: 1.5rem; right: 1.5rem; background: #22c55e;
           color: #fff; padding: .6rem 1rem; border-radius: 8px;
           font-size: .9rem; opacity: 0; transition: opacity .3s; pointer-events: none; }
  .toast.show { opacity: 1; }
  /* Lightbox */
  .lb { display:none; position:fixed; inset:0; background:rgba(0,0,0,.85);
        align-items:center; justify-content:center; z-index:1000; }
  .lb.open { display:flex; }
  .lb img { max-width:92vw; max-height:92vh; border-radius:8px;
             box-shadow:0 4px 32px rgba(0,0,0,.6); }
  .lb-close { position:absolute; top:1.2rem; right:1.5rem; font-size:2rem;
              color:#fff; cursor:pointer; user-select:none; }
</style>
</head>
<body>
<h1>📷 Vespera Photos</h1>
<div class="grid" id="grid"></div>
<div class="empty" id="empty" style="display:none">No photos yet. Send one via Telegram.</div>
<div class="toast" id="toast"></div>
<div class="lb" id="lb"><span class="lb-close" onclick="closeLb()">✕</span><img id="lb-img" src="" /></div>
<script>
  const API = \'\';
  async function load() {
    const r = await fetch(API + \'/api/photos\');
    const data = await r.json();
    const photos = data.photos || [];
    const grid = document.getElementById(\'grid\');
    const empty = document.getElementById(\'empty\');
    grid.innerHTML = \'\';
    if (!photos.length) { empty.style.display = \'\'; return; }
    empty.style.display = \'none\';
    photos.forEach(p => {
      const d = new Date(p.created_at);
      const dateStr = d.toLocaleDateString(\'en-US\', {month:\'short\',day:\'numeric\'})
                    + \' \' + d.toLocaleTimeString(\'en-US\',{hour:\'numeric\',minute:\'2-digit\'});
      const card = document.createElement(\'div\');
      card.className = \'card\';
      const imgSrc = API + \'/api/photos/\' + p.id + \'/image\';
      const captionHtml = p.caption
        ? `<div class="caption">${escHtml(p.caption)}</div>`
        : `<div class="caption empty">(no caption)</div>`;
      card.innerHTML = `
        <img src="${imgSrc}" loading="lazy" alt="photo" onclick="openLb(\'${imgSrc}\')" />
        <div class="card-body">
          ${captionHtml}
          <div class="meta">${dateStr} &nbsp;·&nbsp; ${p.id.slice(0,8)}</div>
        </div>
        <div class="actions">
          <button class="delete-btn" onclick="del(\'${p.id}\')">Delete</button>
        </div>`;
      grid.appendChild(card);
    });
  }
  function escHtml(s) {
    return s.replace(/&/g,\'&amp;\').replace(/</g,\'&lt;\').replace(/>/g,\'&gt;\');
  }
  async function del(id) {
    if (!confirm(\'Delete this photo?\')) return;
    await fetch(API + \'/api/photos/\' + id, {method:\'DELETE\'});
    toast(\'Deleted\');
    load();
  }
  function openLb(src) {
    document.getElementById(\'lb-img\').src = src;
    document.getElementById(\'lb\').classList.add(\'open\');
  }
  function closeLb() { document.getElementById(\'lb\').classList.remove(\'open\'); }
  document.getElementById(\'lb\').addEventListener(\'click\', e => { if (e.target.id===\'lb\') closeLb(); });
  function toast(msg) {
    const t = document.getElementById(\'toast\');
    t.textContent = msg; t.classList.add(\'show\');
    setTimeout(() => t.classList.remove(\'show\'), 2000);
  }
  load();
</script>
</body>
</html>
'''


@app.route("/api/photos")
def get_photos():
    auth_err = require_auth()
    if auth_err: return auth_err
    try:
        limit_raw = request.args.get("limit", 50)
        try:
            limit = max(1, min(int(limit_raw), 500))
        except (ValueError, TypeError):
            limit = 50
        from photos import list_photos, init_photos_db
        init_photos_db()
        return jsonify({"ok": True, "photos": list_photos(limit=limit)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/photos/<photo_id>/image")
def serve_photo(photo_id):
    """Serve the photo image file. No auth — IDs are UUIDs (unguessable)."""
    import re
    photo_id = photo_id.strip().lower()
    if not re.match(r'^[0-9a-f-]{4,36}$', photo_id):
        return jsonify({"ok": False, "error": "Invalid photo id"}), 400
    from photos import get_photo, photo_path, init_photos_db
    init_photos_db()
    record = get_photo(photo_id)
    if not record:
        return jsonify({"ok": False, "error": "Photo not found"}), 404
    from flask import send_file
    path = photo_path(record["filename"])
    if not path.exists():
        return jsonify({"ok": False, "error": "Photo file missing"}), 404
    return send_file(str(path), mimetype="image/jpeg")


@app.route("/api/photos/<photo_id>", methods=["DELETE"])
def remove_photo(photo_id):
    auth_err = require_auth()
    if auth_err: return auth_err
    import re
    photo_id = photo_id.strip().lower()
    if not re.match(r'^[0-9a-f-]{4,36}$', photo_id):
        return jsonify({"ok": False, "error": "Invalid photo id"}), 400
    try:
        from photos import delete_photo, init_photos_db
        init_photos_db()
        ok = delete_photo(photo_id)
        if ok:
            return jsonify({"deleted": True})
        return jsonify({"error": "Photo not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backup", methods=["POST"])
def run_backup():
    auth_err = require_auth()
    if auth_err: return auth_err
    if not _backup_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "Backup already running"}), 409
    try:
        from datetime import datetime
        backups_dir = os.path.join(os.path.dirname(__file__), "backups")
        os.makedirs(backups_dir, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = os.path.join(backups_dir, f"vespera_{ts}.db")
        path = backup_db(dest)
        if not path:
            raise RuntimeError("backup_db returned no path")
    except Exception as e:
        app.logger.error("Backup failed: %s", e)
        return jsonify({"ok": False, "error": "Backup failed"}), 500
    finally:
        _backup_lock.release()
    return jsonify({"ok": True, "backup": os.path.basename(path)})


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
            # Run in a child daemon thread so join(timeout=) gives us a deadline
            # without blocking lock release if the worker hangs.
            worker = threading.Thread(target=_run, daemon=True, name="manual-cleanup-worker")
            worker.start()
            worker.join(timeout=300)
            if worker.is_alive():
                app.logger.error("Manual cleanup timed out after 300s — worker abandoned")
        except Exception:
            app.logger.exception("Manual cleanup failed")
        finally:
            _cleanup_lock.release()  # always releases — even on timeout

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
            worker = threading.Thread(target=_run, daemon=True, name="manual-prune-worker")
            worker.start()
            worker.join(timeout=300)
            if worker.is_alive():
                app.logger.error("Manual pruning timed out after 300s — worker abandoned")
        except Exception:
            app.logger.exception("Manual pruning failed")
        finally:
            _pruning_lock.release()  # always releases — even on timeout

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
    init_db()  # standalone entry point: initialise DB here

    def _handle_sigterm(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)
    # ───────────────────────────────────────────────────────────────────

    base_port = int(os.getenv("API_PORT", "5055"))
    port = base_port

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
