# Vespera 🌙
**A private, self-hosted AI assistant. Runs on your machine. Nobody else controls it.**

[![Support on Ko-fi](https://img.shields.io/badge/Support-Ko--fi-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/problemsolverai2026gmailcom)
![Version](https://img.shields.io/badge/version-1.2.0-blue)

No subscription. No monthly fee. Just your own computer — and an optional API key if you want smarter responses.

---

## Why This Exists

Most AI assistants have a memory problem: every conversation starts from scratch. They don't know your habits, your projects, or what you talked about yesterday.

Vespera solves this by running a persistent memory system on your own machine, 24/7. It thinks quietly in the background, builds context over time, and is still there — knowing what it knows — the next time you talk to it.

It's local-first by design. Your data stays on your machine. Cloud AI is optional and only used when a question is genuinely too complex for the local model.

---

## What It Does

- **Remembers your conversations** — even after restarts, across sessions
- **Thinks in the background** — a local AI model runs quietly 24/7, building context over time
- **Answers questions using web search** — no API key required (DuckDuckGo built in)
- **Hands off complex questions** to a cloud AI if you add a key
- **Texts you reminders** to your phone via Telegram
- **Talks back** with a voice response (TTS works out of the box)
- **Runs tasks on your computer** — file read/write, shell commands (off by default)

---

## What You Need

### Required
- A Mac or Linux computer that stays on
- [Python 3.10+](https://www.python.org/downloads/)
- [Node.js 18+](https://nodejs.org) — for the web UI
- [Ollama](https://ollama.ai) — runs the local AI model

### Optional (all free)
- [Telegram](https://telegram.org) — to text Vespera from your phone
- [Groq API key](https://console.groq.com) — free cloud AI for smarter responses
- [Brave Search API key](https://brave.com/search/api/) — better web search (2,000 free searches/month)

---

## Installation

### Step 1 — Install the prerequisites

- **[Ollama](https://ollama.ai)** — download, install, and open it
- **[Python 3.10+](https://www.python.org/downloads/)**
- **[Node.js 18+](https://nodejs.org)**

### Step 2 — Clone and run setup

```bash
git clone https://github.com/problemsolverai2026-svg/vespera.git
cd vespera
chmod +x setup.sh start.sh
./setup.sh
```

`setup.sh` handles everything: installs Python and UI dependencies, pulls the default Ollama model, creates your `.env`, and optionally sets up auto-start on boot.

### Step 3 — Start Vespera

```bash
./start.sh
```

Open your browser to **http://localhost:3055**

If port 3055 or 5055 is already in use, Vespera automatically picks the next available port.

> **Backend only?** Skip the UI — run `python3 main.py` and `python3 api.py` directly. Telegram and the API still work.

---

## Configuration

Edit `.env` to add optional features. Everything works out of the box with no keys.

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

```bash
# Edit each .plist in launchagents/ and replace REPLACE_WITH_YOUR_PATH
# with the full path to your vespera folder (e.g. /Users/yourname/vespera)

cp launchagents/*.plist ~/Library/LaunchAgents/
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
News/current?  → Web search → local model summarizes
Complex?       → Cloud AI answers (uses API key if set)
     ↓
Response + Voice
     ↓
Background loop thinks quietly, saves useful context to memory
```

Memory is stored in a local SQLite database. It uses a layered structure — `recent → validated → core` — where memories are promoted or pruned over time by the cleanup and pruning components. Nothing leaves your machine unless you've added a cloud API key.

---

## What Works Without API Keys

| Feature | Works without keys? |
|---|---|
| Chat | ✅ local model |
| Web search | ✅ DuckDuckGo built in |
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
CLOUD_MODEL=llama-3.1-8b-instant
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
- **Extensively audited** — 50+ bugs fixed across 13 rounds of parallel Opus + Gemini security review before public release.

---

## Known Limitations

- **Windows is untested** — may work but not supported yet
- **Requires Ollama** — no cloud-only mode without a local model
- **Long-run stability** — not yet tested beyond a few days; memory growth and resource use over weeks is unknown
- **UI is basic** — model selector, API key page, and memory visualization are planned but not built
- **No automated tests** — unit tests not yet written, though the codebase underwent 13 rounds of parallel AI security audit (50+ issues fixed) before release. Contributions welcome.
- **Telegram reminders require `TELEGRAM_ALLOWED_USERS` to be set** — bot denies all access by default for security

---

## Roadmap

- [ ] Docker / docker-compose support
- [ ] Windows compatibility
- [ ] Discord and Signal bot support
- [ ] UI: model selector, API key management, memory graph
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
