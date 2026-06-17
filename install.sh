#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  Vespera Installer
#  Runs once. Sets everything up so you can just run ./start.sh
# ═══════════════════════════════════════════════════════════

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Colors ────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}  ✅  $1${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️   $1${RESET}"; }
err()  { echo -e "${RED}  ❌  $1${RESET}"; }
info() { echo -e "${BLUE}  ℹ️   $1${RESET}"; }
sep()  { echo -e "\n${BOLD}──────────────────────────────────────────${RESET}"; }

echo ""
echo -e "${BOLD}🌙 Vespera Installer${RESET}"
echo "   This will set up everything you need."
echo "   It only needs to run once."
echo ""

# ═══════════════════════════════════════════════════════════
#  STEP 1 — Python 3
# ═══════════════════════════════════════════════════════════
sep
echo -e "${BOLD}Step 1 of 5 — Python 3${RESET}"
echo ""

if command -v python3 &>/dev/null; then
  PY_VER=$(python3 --version 2>&1)
  ok "Python 3 is installed. ($PY_VER)"
else
  err "Python 3 is not installed."
  echo ""
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  To install it on a Mac:"
    echo ""
    echo "     1. Go to https://python.org/downloads"
    echo "     2. Click the big yellow Download button"
    echo "     3. Open the downloaded file and follow the installer"
    echo "     4. Come back here and run this script again"
  elif [[ "$OSTYPE" == "linux"* ]]; then
    echo "  Run this in your terminal:"
    echo "     sudo apt install python3 python3-pip python3-venv"
  else
    echo "  Download Python from https://python.org/downloads"
  fi
  echo ""
  exit 1
fi

# ═══════════════════════════════════════════════════════════
#  STEP 1.5 — Node.js (for the web UI)
# ═══════════════════════════════════════════════════════════
sep
echo -e "${BOLD}Step 2 of 5 — Node.js (web interface)${RESET}"
echo ""
echo "  Node.js powers Vespera's web interface."
echo ""

if command -v node &>/dev/null; then
  NODE_VER=$(node --version 2>&1)
  ok "Node.js is installed. ($NODE_VER)"
else
  warn "Node.js is not installed."
  echo ""
  if [[ \"$OSTYPE\" == \"darwin\"* ]]; then
    echo "  To install it on a Mac:"
    echo ""
    echo "     1. Go to https://nodejs.org"
    echo "     2. Click the big green LTS (Recommended) button to download"
    echo "     3. Open the downloaded file and follow the installer"
    echo "     4. Come back here and run this script again"
  elif [[ \"$OSTYPE\" == \"linux\"* ]]; then
    echo "  Run this in your terminal:"
    echo "     curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
    echo "     sudo apt install -y nodejs"
  fi
  echo ""
  exit 1
fi

# ═══════════════════════════════════════════════════════════
#  STEP 3 — Ollama (the AI engine)
# ═══════════════════════════════════════════════════════════
sep
echo -e "${BOLD}Step 3 of 5 — Ollama (the AI engine)${RESET}"
echo ""
echo "  Vespera uses Ollama to run AI locally on your computer."
echo "  Your conversations never leave your machine."
echo ""

if command -v ollama &>/dev/null; then
  ok "Ollama is installed."
else
  warn "Ollama is not installed."
  echo ""
  if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  Installing Ollama now..."
    if command -v brew &>/dev/null; then
      brew install --cask ollama
    else
      echo ""
      echo "  Please install it manually:"
      echo "     1. Go to https://ollama.ai"
      echo "     2. Click Download"
      echo "     3. Open the downloaded file and drag Ollama to your Applications folder"
      echo "     4. Open Ollama from your Applications folder (it will appear in your menu bar)"
      echo "     5. Come back here and run this script again"
      echo ""
      exit 1
    fi
  elif [[ "$OSTYPE" == "linux"* ]]; then
    echo "  Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
  else
    echo "  Download Ollama from https://ollama.ai and install it, then run this script again."
    exit 1
  fi
fi

# Start Ollama if it's not running
if ! curl -s http://localhost:11434 &>/dev/null; then
  info "Starting Ollama..."
  if [[ "$OSTYPE" == "darwin"* ]]; then
    open -a Ollama 2>/dev/null || ollama serve &>/dev/null &
  else
    ollama serve &>/dev/null &
  fi
  echo "  Waiting for Ollama to start..."
  for i in $(seq 1 20); do
    if curl -s http://localhost:11434 &>/dev/null; then break; fi
    sleep 1
  done
fi

if ! curl -s http://localhost:11434 &>/dev/null; then
  err "Ollama started but isn't responding. Try opening Ollama manually, then run this script again."
  exit 1
fi
ok "Ollama is running."

# Download the AI model if needed
echo ""
if ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
  ok "AI model (llama3.2:3b) is already downloaded."
else
  info "Downloading the AI model — this is a one-time 2 GB download. Please wait..."
  echo ""
  ollama pull llama3.2:3b
  ok "AI model downloaded."
fi

# ═══════════════════════════════════════════════════════════
#  STEP 4 — Python packages
# ═══════════════════════════════════════════════════════════
sep
echo -e "${BOLD}Step 4 of 5 — Python packages${RESET}"
echo ""

VENV_DIR="$SCRIPT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
  info "Creating isolated Python environment..."
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

info "Installing Python packages (this takes a minute the first time)..."
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
ok "Python packages installed."

# ── UI dependencies ──────────────────────────────────────
if [ -d "$SCRIPT_DIR/ui" ]; then
  info "Installing web UI dependencies (one time only)..."
  cd "$SCRIPT_DIR/ui" && npm install --silent && cd "$SCRIPT_DIR"
  ok "Web UI ready."
fi

# ═══════════════════════════════════════════════════════════
#  STEP 5 — Configuration
# ═══════════════════════════════════════════════════════════
sep
echo -e "${BOLD}Step 5 of 5 — Configuration${RESET}"
echo ""

if [ ! -f "$SCRIPT_DIR/.env" ]; then
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
  ok "Created your settings file (.env)."
else
  ok "Settings file already exists."
fi

# ── Optional: Telegram bot token ──────────────────────────
echo ""
echo "  Vespera can send you messages on Telegram (optional)."
read -p "  Do you have a Telegram bot token you'd like to add? [y/N] " add_telegram
if [[ "$add_telegram" =~ ^[Yy]$ ]]; then
  read -p "  Paste your token here: " tel_token
  if [[ -n "$tel_token" ]]; then
    # Replace or add TELEGRAM_BOT_TOKEN in .env
    if grep -q "^TELEGRAM_BOT_TOKEN=" "$SCRIPT_DIR/.env"; then
      sed -i.bak "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$tel_token|" "$SCRIPT_DIR/.env"
    else
      echo "TELEGRAM_BOT_TOKEN=$tel_token" >> "$SCRIPT_DIR/.env"
    fi
    ok "Telegram token saved."
  fi
fi

# ── Optional: auto-start on login (macOS) ────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
  echo ""
  echo "  Vespera can start automatically every time you log into your Mac."
  read -p "  Would you like that? [y/N] " autostart
  if [[ "$autostart" =~ ^[Yy]$ ]]; then
    for plist in "$SCRIPT_DIR/launchagents/"*.plist; do
      [ -f "$plist" ] || continue
      dest="$HOME/Library/LaunchAgents/$(basename "$plist")"
      sed "s|REPLACE_WITH_YOUR_PATH|$SCRIPT_DIR|g" "$plist" > "$dest"
      launchctl load "$dest" 2>/dev/null || true
    done
    ok "Vespera will start automatically on login."
    info "(To undo: launchctl unload ~/Library/LaunchAgents/com.vespera.*.plist)"
  fi
fi

# ═══════════════════════════════════════════════════════════
#  Done!
# ═══════════════════════════════════════════════════════════
sep
echo ""
echo -e "${GREEN}${BOLD}🎉 You're all set!${RESET}"
echo ""
echo "  To start Vespera, run:"
echo ""
echo -e "     ${BOLD}./start.sh${RESET}"
echo ""
echo "  Then open your browser and go to:"
echo ""
echo -e "     ${BOLD}http://localhost:3055${RESET}"
echo ""
echo "  To install Vespera on your phone, go to:"
echo ""
echo -e "     ${BOLD}http://localhost:3055/phone-setup${RESET}"
echo ""
