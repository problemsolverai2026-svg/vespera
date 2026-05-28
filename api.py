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
from config import COMPONENTS, get_component, COMPLEXITY_THRESHOLD, PRUNING_INTERVAL_DAYS
from memory.store import (
    init_db, get_memories, get_recent_conversations,
    get_stats, add_conversation,
)

app = Flask(__name__)
CORS(app)  # allow Lovable frontend to connect

init_db()


# ─────────────────────────────────────────────
# STATUS
# ─────────────────────────────────────────────

@app.route("/api/status")
def status():
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
    layer = request.args.get("layer")
    limit = int(request.args.get("limit", 20))
    memories = get_memories(layer=layer, limit=limit)
    return jsonify(memories)


@app.route("/api/conversations")
def list_conversations():
    limit = int(request.args.get("limit", 20))
    convs = get_recent_conversations(limit=limit)
    return jsonify(convs)


# ─────────────────────────────────────────────
# CHAT
# ─────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.json or {}
    message = data.get("message", "").strip()
    if not message:
        return jsonify({"ok": False, "error": "No message provided"}), 400

    from handoff import handle_message
    add_conversation(role="user", content=message)
    result = handle_message(message)
    add_conversation(role="assistant", content=result["response"])

    return jsonify({
        "ok": True,
        "response": result["response"],
        "handled_by": result["handled_by"],
        "complexity": result["complexity"],
    })


# ─────────────────────────────────────────────
# MANUAL TRIGGERS
# ─────────────────────────────────────────────

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
    port = int(os.getenv("API_PORT", "5050"))
    print(f"[Vespera API] Running on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
