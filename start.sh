#!/bin/bash
# ─────────────────────────────────────────────
# Vespera Startup Script
# Starts the API and UI together, auto-resolving port conflicts.
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UI_DIR="${VESPERA_UI_DIR:-$SCRIPT_DIR/ui}"
VENV_DIR="$SCRIPT_DIR/venv"

# Activate virtual environment if it exists
if [ -d "$VENV_DIR" ]; then
    source "$VENV_DIR/bin/activate"
fi

echo "Starting Vespera..."

# Check Python3
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3 not found. Install it from https://python.org"
    exit 1
fi

# Check Node.js (needed for web UI)
if ! command -v npm &>/dev/null; then
    echo "❌ npm not found. Install Node.js from https://nodejs.org"
    exit 1
fi

# Check Ollama
if ! curl -s http://localhost:11434 &>/dev/null; then
    echo "⚠️  Ollama is not running. Start Ollama first, then run ./start.sh"
    echo "   Download: https://ollama.ai"
    exit 1
fi

# Check .env exists
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    echo "⚠️  No .env file found. Creating one from .env.example..."
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    echo "   Edit $SCRIPT_DIR/.env to add your API keys, then run ./start.sh again."
    exit 1
fi

# Start API in background
python3 "$SCRIPT_DIR/api.py" &
API_PID=$!

# Wait for API to write its port file (up to 15s)
echo -n "Waiting for API..."
for i in $(seq 1 30); do
    if [ -f "$SCRIPT_DIR/.port" ]; then break; fi
    sleep 0.5
done

# Read actual API port
if [ -f "$SCRIPT_DIR/.port" ]; then
    API_PORT=$(cat "$SCRIPT_DIR/.port")
else
    API_PORT=5055
fi

# Wait until API actually responds (up to 10s more)
for i in $(seq 1 20); do
    if curl -sf "http://localhost:$API_PORT/health" &>/dev/null; then break; fi
    sleep 0.5
done
echo " ready on port $API_PORT"

# Write port for UI to read
echo "VITE_API_PORT=$API_PORT" > "$UI_DIR/.env.local"

# Start background loop
python3 "$SCRIPT_DIR/main.py" &
MAIN_PID=$!

# Start Telegram bot if token is set
if grep -q "^TELEGRAM_BOT_TOKEN=" "$SCRIPT_DIR/.env" 2>/dev/null; then
    python3 "$SCRIPT_DIR/telegram_bot.py" &
    TEL_PID=$!
    echo "Telegram bot started."
fi

# Start UI — check node_modules exist (means npm install has been run)
if [ -d "$UI_DIR/node_modules" ]; then
    cd "$UI_DIR"
    npm run dev -- --port 3055 &
    UI_PID=$!
    # Wait until UI dev server is up (up to 15s)
    echo -n "Waiting for UI..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:3055" &>/dev/null; then break; fi
        sleep 0.5
    done
    echo " ready"
    echo ""
    echo "✅ Vespera is running!"
    echo "   Web UI: http://localhost:3055"
    echo "   API:    http://localhost:$API_PORT"
    echo ""
    echo "   Press Ctrl+C to stop everything."
elif [ -d "$UI_DIR" ]; then
    echo ""
    echo "⚠️  Web UI found but dependencies not installed."
    echo "   Run: cd ui && npm install && cd .."
    echo "   Then restart with: ./start.sh"
    echo ""
    echo "✅ Vespera is running (API only)!"
    echo "   API: http://localhost:$API_PORT"
    echo ""
    echo "   Press Ctrl+C to stop everything."
else
    echo ""
    echo "✅ Vespera is running!"
    echo "   API: http://localhost:$API_PORT"
    echo "   (No web UI found.)"
    echo ""
    echo "   Press Ctrl+C to stop everything."
fi

# Wait and clean up on exit
trap "kill $API_PID $MAIN_PID ${TEL_PID:-} ${UI_PID:-} 2>/dev/null; echo 'Vespera stopped.'" EXIT
wait
