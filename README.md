# Vespera 🌙
**A private, self-hosted AI assistant. Runs on your machine. Nobody else controls it.**

[![Support on Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/problemsolverai2026gmailcom)
![Version](https://img.shields.io/badge/version-1.5.0-blue)

No subscription. No monthly fee. Just your own computer — and an optional API key if you want smarter responses.

---

## Why This Exists

Most AI assistants have a memory problem: every conversation starts from scratch. They don't know your habits, your projects, or what you talked about yesterday.

Vespera solves this by running a persistent memory system on your own machine, 24/7. It thinks quietly in the background, builds context over time, and is still there — knowing what it knows — the next time you talk to it.

It's local-first by design. Your data stays on your machine. Cloud AI is optional and only used when a question is genuinely too complex for the local model.

---

## What It Does

- **Remembers your conversations** — facts you share are extracted and stored automatically, surviving restarts and sessions
- **Thinks in the background** — a local AI model runs quietly 24/7, forming its own thoughts and follow-up questions based on what you've talked about
- **Follows up when you return** — after a gap, Vespera picks up where you left off and asks about something you mentioned before
- **Answers questions using web search** — no API key required (DuckDuckGo built in)
- **Live financial prices** — silver, gold, bitcoin, stocks, oil and more via Yahoo Finance (no API key, always free)
- **Hands off complex questions** to a cloud AI if you add a key
- **Texts you reminders** to your phone via Telegram
- **Talks back** with a voice response (TTS works out of the box)
- **Runs tasks on your computer** — file read/write, shell commands (off by default)

---

## What You Need

- A **Mac or Linux computer** that can stay on (Windows support coming soon)
- **[Python 3.10+](https://www.python.org/downloads/)** — free, takes 2 minutes to install
- That's it. The installer handles the rest.

> **Node.js is optional.** Vespera includes a built-in web UI that runs without it. If you have Node.js installed, a more feature-rich React UI is also available.

---

## Installation

### Step 1 — Download Vespera

Click the green **Code** button at the top of this page → **Download ZIP**.

Unzip the file. You'll get a folder called `vespera-main`.

### Step 2 — Open a Terminal

**On Mac:** Press `Command + Space`, type `Terminal`, hit Enter.

**On Linux:** Right-click the desktop → Open Terminal (varies by distro).

### Step 3 — Run the installer

In the terminal, type these two lines (one at a time, press Enter after each):

```bash
cd ~/Downloads/vespera-main
./install.sh
```

The installer will walk you through everything — including downloading the AI model (about 2 GB, one time only). Just follow the prompts.

### Step 4 — Start Vespera

```bash
./start.sh
```

Your browser will open automatically. Or navigate to **http://localhost:5055** manually.

That's it. You're running your own private AI.

---

### 📱 Install on your phone

Once Vespera is running, open **http://localhost:5055** and click the **📱 Phone** tab.
It shows step-by-step instructions for iPhone and Android — no app store needed.

> **Advanced:** Backend only? Run `python3 api.py` and `python3 main.py` directly. Telegram and the API still work.

---

## Configuration

Edit `.env` to add optional features. Everything works out of the box with no keys.

**Access from your phone without Telegram:**

Vespera's web UI works in any mobile browser — no app install needed.

- **Same WiFi:** Find your computer's local IP (`System Settings → Wi-Fi → Details` on Mac, or `hostname -I` on Linux), then open `http://<your-ip>:5055` on your phone.
- **From anywhere (Tailscale):** Install [Tailscale](https://tailscale.com) on both your computer and phone (free). Once connected, open `http://<tailscale-ip>:5055` on your phone — works on any network, not just home WiFi.

Your Tailscale IP can be found at [login.tailscale.com](https://login.tailscale.com) or by running `tailscale ip` in a terminal.

---

**To add Telegram** (text from your phone):
1. Open Telegram and message **@BotFather**
2. Send `/newbot` — give it a name and username (must end in `bot`)
3. Copy the token to `TELEGRAM_BOT_TOKEN=` in `.env`
4. Message **@userinfobot** on Telegram to get your user ID
5. Add it to `TELEGRAM_ALLOWED_USERS=` in `.env`
6. Multiple people: `TELEGRAM_ALLOWED_USERS=id1,id2`

**To add cloud AI** (smarter responses):
- Groq (free, fast): [console.groq.com](https://console.groq.com) → set `CLOUD_API_KEY=` and `CLOUD_PROVIDER=groq`
- Anthropic Claude: [console.anthropic.com](https://console.anthropic.com) → set `CLOUD_API_KEY=` and `CLOUD_PROVIDER=claude`

**Better Ollama model** (if you have the RAM):
```bash
ollama pull qwen2.5:7b    # ~8GB RAM — better quality
ollama pull qwen2.5:14b   # ~16GB RAM — best local quality
```
Then set `BACKGROUND_OLLAMA_MODEL=qwen2.5:7b` in `.env`.
---

## Auto-Start on Boot

### macOS (LaunchAgents)

**Recommended:** run `./install.sh` — it will ask whether to set up auto-start and handles the path substitution automatically.

**Manual (if you skipped install.sh):**
```bash
# Substitute your actual path into the plist files first:
for plist in launchagents/*.plist; do
    sed "s|REPLACE_WITH_YOUR_PATH|$(pwd)|g" "$plist" \
        > ~/Library/LaunchAgents/$(basename "$plist")
done
for f in ~/Library/LaunchAgents/com.vespera.*.plist; do launchctl load "$f"; done
```

Vespera starts automatically every time your Mac turns on.

### Linux (systemd)

```bash
cp systemd/*.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable vespera-main vespera-api vespera-telegram
systemctl --user start vespera-main vespera-api vespera-telegram
```

---

## How It Works

```
Your message
     ↓
Local model scores complexity
     ↓
Simple?        → Local model answers (free, instant)
Price question? → Yahoo Finance (free, no key needed)
News/current?  → Web search → local model summarizes
Complex?       → Cloud AI answers (uses API key if set)
     ↓
Response + Voice
     ↓
Background loop thinks quietly, saves useful context to memory
```

Memory is stored in a local SQLite database. It uses a four-layer nesting-doll structure — `working → recent → validated → core` — where memories are promoted or pruned over time by the cleanup and pruning components. Nothing leaves your machine unless you've added a cloud API key.

---

## What Works Without API Keys

| Feature | Works without keys? |
|---|---|
| Chat | ✅ local model |
| Web search | ✅ DuckDuckGo built in |
| Live prices (silver, gold, crypto, stocks) | ✅ Yahoo Finance, no key needed |
| Memory | ✅ fully local SQLite |
| TTS voice | ✅ edge-tts (free, Microsoft neural voices) |
| Reminders | ✅ fully local |
| Telegram | ✅ free bot token |
| File/shell tools | ✅ shell off by default — enable in `.env` |
| Smarter AI | ❌ needs a cloud API key |
| Premium voice | ❌ needs Venice key |

---

## Privacy

- Everything runs on your machine
- No data is sent anywhere unless you add a cloud API key
- With a cloud key: only your message + minimal memory context is sent to the API
- Your memory database never leaves your computer

---

## Logs

**When using `./start.sh`:** logs print directly to your terminal window.

**When using auto-start (LaunchAgents / systemd):** logs go to files:
```bash
tail -f /tmp/vespera.log          # background loop
tail -f /tmp/vespera-api.log      # API server
tail -f /tmp/vespera-telegram.log # Telegram bot
```

---

## API Resources & Costs

Everything below is optional. Vespera works without any of them.

### Cloud AI

| Provider | Free Tier | Link |
|---|---|---|
| **Groq** | ✅ Free (fast, open-source models) | [console.groq.com](https://console.groq.com) |
| **Google Gemini** | ✅ Free tier | [aistudio.google.com](https://aistudio.google.com) |
| **Venice AI** | ✅ Free tier | [venice.ai](https://venice.ai) |
| **Anthropic Claude** | ❌ No free tier | [console.anthropic.com](https://console.anthropic.com) |
| **OpenAI** | ❌ No free tier | [platform.openai.com](https://platform.openai.com) |

**Recommendation:** Start with Groq (free, no credit card) or Gemini (free tier). Upgrade to Claude when you want the best quality.

### Web Search

| Provider | Free Tier | Link |
|---|---|---|
| **DuckDuckGo** | ✅ Always free, no key | Built in |
| **Brave Search** | ✅ 2,000 searches/month free | [brave.com/search/api](https://brave.com/search/api/) |

### Voice / TTS

| Provider | Free | Quality | Link |
|---|---|---|---|
| **edge-tts** | ✅ Always free | Good | Built in |
| **kokoro-onnx** | ✅ Fully local (~80MB, auto-downloads) | Good | Built in |
| **Venice AI** | ✅ Free tier | Best | [venice.ai](https://venice.ai) |

### Telegram

Free. [telegram.org](https://telegram.org) — bot setup takes 2 minutes via @BotFather.

---

## Cheapest Possible Setup (fully free)

```bash
# In .env:
CLOUD_PROVIDER=groq
CLOUD_API_KEY=your_groq_key_here
CLOUD_MODEL=llama3-8b-8192
```

Sign up at [console.groq.com](https://console.groq.com) — no credit card required.

---

## Docs

- [How memory works](docs/memory.md) — the nesting doll system explained
- [Architecture diagram](docs/architecture.html) — full system overview

---

## Security

- **Shell execution** is disabled by default (`VESPERA_ALLOW_SHELL=false`). When enabled, the AI can run shell commands on your machine — only enable this if you understand the risk.
- **File access** is restricted to configured paths only (`VESPERA_ALLOW_PATHS`).
- **API access** can be token-protected via `VESPERA_API_TOKEN`.
- **Telegram** can be restricted to specific user IDs via `TELEGRAM_ALLOWED_USERS`.
- **Web search results** are sanitized before being fed into model prompts to reduce prompt injection risk.
- **Cloud APIs** only receive your message and minimal memory context — your full memory database never leaves your machine.
- **Extensively audited** — 90+ bugs fixed across 26+ rounds of parallel Opus + Gemini + Grok security review before public release.

---

## Known Limitations

- **Windows is untested** — may work but not supported yet
- **Requires Ollama** — no cloud-only mode without a local model
- **Long-run stability** — not yet tested beyond a few days; memory growth and resource use over weeks is unknown
- **UI is desktop-first** — use the mobile PWA (`/app`) for the best phone experience
- **No automated tests** — unit tests not yet written, though the codebase underwent 15+ rounds of parallel AI security audit (60+ issues fixed) before release. Contributions welcome.
- **Telegram reminders require `TELEGRAM_ALLOWED_USERS` to be set** — bot denies all access by default for security

---

## Roadmap

- [ ] Docker / docker-compose support
- [ ] Windows compatibility
- [ ] Discord and Signal bot support
- [x] UI: model selector, API key management, memory visualization
- [ ] Automated test suite
- [ ] Long-run stability hardening
- [ ] Multi-user support
- [ ] Federated memory sharing — opt-in exchange of validated memories between Vespera instances, so knowledge can spread across nodes while personal data stays local. All shared memories are human-gated: nothing leaves your node without your explicit approval.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for where to start, what needs help, and how to submit changes.

---

## License

MIT — free to use, modify, and share.
