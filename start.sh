#!/bin/bash
# ─────────────────────────────────────────────
# Vespera Startup Script
# Starts the API and UI together, auto-resolving port conflicts.
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UI_DIR="${VESPERA_UI_DIR:-$SCRIPT_DIR/../vespera-memory-hub}"

echo "Starting Vespera..."

# Start API in background
python3 "$SCRIPT_DIR/api.py" &
API_PID=$!
sleep 3

# Read actual API port (api.py writes this on startup)
if [ -f "$SCRIPT_DIR/.port" ]; then
    API_PORT=$(cat "$SCRIPT_DIR/.port")
else
    API_PORT=5055
fi
echo "API running on port $API_PORT"

# Write port for UI to read
echo "VITE_API_PORT=$API_PORT" > "$UI_DIR/.env.local"

# Start background loop
python3 "$SCRIPT_DIR/main.py" &
MAIN_PID=$!

# Start Telegram bot if token is set
if grep -q "TELEGRAM_BOT_TOKEN=." "$SCRIPT_DIR/.env" 2>/dev/null; then
    python3 "$SCRIPT_DIR/telegram_bot.py" &
    TEL_PID=$!
    echo "Telegram bot started."
fi

# Start UI (Vite auto-picks next port if 3055 is taken)
if [ -d "$UI_DIR" ]; then
    cd "$UI_DIR"
    npm run dev -- --port 3055 &
    UI_PID=$!
    sleep 3
    echo ""
    echo "✅ Vespera is running!"
    echo "   Open your browser: http://localhost:3055"
    echo ""
    echo "   Press Ctrl+C to stop everything."
fi

# Wait and clean up on exit
trap "kill $API_PID $MAIN_PID ${TEL_PID:-} ${UI_PID:-} 2>/dev/null; echo 'Vespera stopped.'" EXIT
wait
