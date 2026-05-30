#!/bin/bash
# ─────────────────────────────────────────────
# Vespera Setup Script
# Run once before start.sh — installs deps and configures auto-start.
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"

echo "🌙 Vespera Setup"
echo ""

# ── Python check ──────────────────────────────
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 not found. Install it from https://python.org"
    exit 1
fi

# ── Node check ────────────────────────────────
if ! command -v npm &>/dev/null; then
    echo "❌ npm not found. Install Node.js from https://nodejs.org"
    exit 1
fi

# ── Ollama check ──────────────────────────────
if ! curl -s http://localhost:11434 &>/dev/null; then
    echo "⚠️  Ollama is not running."
    echo "   Install from https://ollama.ai, then run: ollama pull llama3.2:3b"
    echo "   Once Ollama is running, re-run this script."
    exit 1
fi

# ── Virtual environment ───────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

# ── Pull default model if not present ─────────
if ! ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
    echo "📦 Pulling llama3.2:3b (2GB, one time only)..."
    ollama pull llama3.2:3b
fi

# ── Python dependencies ───────────────────────
echo "📦 Installing Python dependencies..."
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

# ── UI dependencies ───────────────────────────
if [ -d "$SCRIPT_DIR/ui" ]; then
    echo "📦 Installing UI dependencies..."
    cd "$SCRIPT_DIR/ui" && npm install --silent && cd "$SCRIPT_DIR"
fi

# ── .env ──────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "📝 Creating .env from .env.example..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "   ✅ Created .env — edit it to add your API keys (optional)"
fi

# ── macOS LaunchAgents ────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo ""
    read -p "Set up auto-start on boot (macOS LaunchAgents)? [y/N] " autostart
    if [[ "$autostart" =~ ^[Yy]$ ]]; then
        for plist in "$SCRIPT_DIR/launchagents/"*.plist; do
            dest="$HOME/Library/LaunchAgents/$(basename "$plist")"
            sed "s|REPLACE_WITH_YOUR_PATH|$SCRIPT_DIR|g" "$plist" > "$dest"
            launchctl load "$dest" 2>/dev/null
            echo "   Loaded: $(basename "$dest")"
        done
        echo "   ✅ Vespera will now start automatically on login."
        echo "   (To remove: launchctl unload ~/Library/LaunchAgents/com.vespera.*.plist)"
    fi
fi

echo ""
echo "✅ Setup complete! Run ./start.sh to launch Vespera."
echo "   Then open: http://localhost:3055"
