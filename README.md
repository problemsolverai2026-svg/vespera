# Vespera 🌙
**A private, self-hosted AI assistant. Runs on your machine. Nobody else controls it.**

No subscription. No monthly fee. Just your own computer and an optional API key.

---

## What It Does
- Remembers your conversations — even after restarts
- Answers questions using web search (no key required)
- Runs tasks on your computer (files, shell commands)
- Texts you reminders to your phone via Telegram
- Talks back with a voice response
- Thinks in the background 24/7 using a local AI model
- Hands off complex questions to a cloud AI (if you add an API key)

---

## What You Need

### Required
- A Mac or Linux computer that stays on
- [Python 3.10+](https://www.python.org/downloads/)
- [Ollama](https://ollama.ai) — runs the local AI model

### Optional (free)
- [Telegram](https://telegram.org) — to text Vespera from your phone
- [Brave Search API key](https://brave.com/search/api/) — better web search (2000 free searches/month)

### Optional (paid)
- [Anthropic API key](https://console.anthropic.com) — for Claude (smarter responses on complex questions)
- [Venice AI key](https://venice.ai) — for higher quality voice

---

## Installation

### Step 1 — Install Ollama
Go to [ollama.ai](https://ollama.ai) and download Ollama for your system. Install and open it.

Then download the AI model (this takes a few minutes):
```bash
ollama pull qwen2.5:14b
```

### Step 2 — Download Vespera
```bash
git clone https://github.com/problemsolverai2026-svg/vespera.git
cd vespera
```

### Step 3 — Install Python dependencies
```bash
pip3 install -r requirements.txt
```

### Step 4 — Configure
```bash
cp .env.example .env
```
Open `.env` in any text editor and fill in your values.

**Minimum required to get started** (everything else is optional):
- Nothing — it works out of the box with just Ollama installed.

**To add Claude** (smarter responses):
- Get a free API key at [console.anthropic.com](https://console.anthropic.com)
- Add it as `CLOUD_API_KEY=` in your `.env`

**To add Telegram** (text from your phone):

1. Download Telegram: [telegram.org](https://telegram.org) — free on iPhone, Android, and desktop
2. Create an account if you don't have one
3. In Telegram, search for **@BotFather** and open that chat
4. Send it the message: `/newbot`
5. It asks for a name — type anything (example: `My Vespera`)
6. It asks for a username — must end in `bot` (example: `MyVespera_bot`)
7. BotFather gives you a token like: `1234567890:ABCdefGHI...`
8. Copy that token into your `.env`: `TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHI...`
9. Now get your Telegram user ID — search for **@userinfobot** in Telegram and send any message
10. It replies with your ID number (example: `1234567890`)
11. Add it to your `.env`: `TELEGRAM_ALLOWED_USERS=1234567890`
    - This locks your bot so only you can use it. Anyone else gets "Access denied."
    - Two people? Separate with a comma: `TELEGRAM_ALLOWED_USERS=1234567890,9876543210`
12. Restart Vespera, search for your bot by username in Telegram, and start chatting

### Step 5 — Start Vespera
```bash
./start.sh
```
That's it. One command starts everything — the API, background loop, Telegram bot, and web UI.
Then open your browser to: **http://localhost:3055**

If port 3055 or 5055 is already in use on your machine, Vespera automatically picks the next available port and tells you which one it used.

### Step 6 — Auto-start on boot (survives power outages)

**macOS:**
```bash
# First edit each .plist file in the launchagents/ folder
# and replace REPLACE_WITH_YOUR_PATH with the full path to your vespera folder
# Example: /Users/yourname/vespera

cp launchagents/*.plist ~/Library/LaunchAgents/
for f in ~/Library/LaunchAgents/com.vespera.*.plist; do launchctl load "$f"; done
```
Vespera will now start automatically every time your Mac turns on.

**Linux (systemd):**
```bash
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable vespera-main vespera-api vespera-telegram vespera-ui
systemctl --user start vespera-main vespera-api vespera-telegram vespera-ui
```
Vespera will now start automatically on every reboot, including after power outages.

---

## Talking to Vespera

**Web UI:** Open your browser and go to `http://localhost:3055`

**Telegram:** Message your bot directly from your phone

**Stop Vespera:** `vespera-stop`
**Start Vespera:** `vespera-start`

---

## How It Works

```
Your message
     ↓
Local model scores complexity
     ↓
Simple question?  → Local model answers (free, instant)
News/current?     → Web search → Local model summarizes
Complex?          → Cloud AI answers (uses API key)
     ↓
Response + Voice
```

Memory is saved to a local SQLite database. The background loop thinks quietly every 3 minutes, building context about your conversations over time.

---

## What Works With No API Keys

| Feature | Works without keys? |
|---------|-------------------|
| Chat | ✅ (local model) |
| Web search | ✅ (DuckDuckGo) |
| Memory | ✅ (fully local) |
| File/shell tools | ✅ (shell off by default — enable in .env) |
| TTS voice | ✅ (edge-tts or kokoro-onnx) |
| Reminders | ✅ (fully local) |
| Telegram | ✅ (free bot token) |
| Smarter AI | ❌ (needs Anthropic key) |
| Premium voice | ❌ (needs Venice key) |

---

## Privacy
- Everything runs on your machine
- No data is sent anywhere unless you add a cloud API key
- With a cloud key: only the message + minimal context is sent to the API
- Your memory database never leaves your computer

---

## Logs
```bash
tail -f /tmp/vespera.log          # background loop
tail -f /tmp/vespera-api.log      # API server
tail -f /tmp/vespera-telegram.log # Telegram bot
```

---

## API Resources & Costs

Everything below is optional. Vespera works without any of them.

### 🤖 Cloud AI — for smarter responses on complex questions

| Provider | Free Tier | Cheapest Paid | Link |
|----------|-----------|---------------|------|
| **Anthropic (Claude)** | No free tier | ~$0.003/1K tokens (Haiku) | [console.anthropic.com](https://console.anthropic.com) |
| **Google Gemini** | ✅ Free tier available | Very cheap | [aistudio.google.com](https://aistudio.google.com) |
| **Groq** | ✅ Free tier (fast) | Free for most use | [console.groq.com](https://console.groq.com) |
| **Venice AI** | ✅ Free tier | Low cost | [venice.ai](https://venice.ai) |

**Recommendation:** Start with **Groq** (free, fast) or **Google Gemini** (free tier). Add Anthropic Claude when you want the best quality.

To use a different provider, set in your `.env`:
```
CLOUD_PROVIDER=claude    # or: venice, openai
CLOUD_MODEL=claude-haiku-3-5   # cheaper Claude model
```

---

### 🔍 Web Search

| Provider | Free Tier | Link |
|----------|-----------|------|
| **DuckDuckGo** | ✅ Always free, no key needed | Built in |
| **Brave Search** | ✅ 2,000 free searches/month | [brave.com/search/api](https://brave.com/search/api/) |

**Recommendation:** DuckDuckGo works great out of the box. Add Brave for better results.

---

### 🎙️ Voice / TTS

| Provider | Free Tier | Quality | Link |
|----------|-----------|---------|------|
| **edge-tts** | ✅ Always free | Good | Built in (Microsoft neural voices) |
| **kokoro-onnx** | ✅ Always free, fully local | Good | Auto-downloads on first use (~80MB) |
| **Venice AI** | ✅ Free tier | Best | [venice.ai](https://venice.ai) |

**Recommendation:** Works out of the box with no key. Add Venice for the best voice quality.

---

### 📱 Telegram

- **Cost:** Completely free
- **Download:** [telegram.org](https://telegram.org)
- **Bot setup:** Message [@BotFather](https://t.me/BotFather) on Telegram — takes 2 minutes

---

### 💡 Cheapest Possible Setup
If you want cloud AI without spending money:
1. Sign up for **Groq** (free) — [console.groq.com](https://console.groq.com)
2. Set in `.env`:
```
CLOUD_PROVIDER=openai
CLOUD_BASE_URL=https://api.groq.com/openai/v1
CLOUD_API_KEY=your_groq_key
CLOUD_MODEL=llama-3.1-8b-instant
```
Groq runs open-source models for free at high speed. No credit card required.

---

## Contributing

Pull requests welcome. For major changes, open an issue first.

Areas where help is most needed:
- Additional messaging platforms (Discord, Signal, WhatsApp)
- Docker/docker-compose support
- Windows compatibility
- UI improvements

---

## Coming Soon
- Discord bot support
- Signal messaging
- Browser control
- More messaging platforms

---

## License
MIT — free to use, modify, and share.
