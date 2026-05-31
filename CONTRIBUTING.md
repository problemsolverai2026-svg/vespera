# Contributing to Vespera

Thanks for your interest. Vespera is a solo open-source project and contributions are welcome.

---

## Where to Start

The codebase is split into focused files — pick one area and go deep:

| File | What it does |
|---|---|
| `memory/store.py` | SQLite memory store — reading, writing, promoting, pruning |
| `background_loop.py` | Background thinking engine — runs every 3 minutes |
| `cleanup_crew.py` | First-pass memory reviewer — runs every 5 minutes |
| `periodic_pruning.py` | Deep memory review — runs every 3 days |
| `handoff.py` | Routes messages between local and cloud models |
| `scheduler.py` | Reminder parsing and delivery |
| `tts.py` | Text-to-speech with fallback chain |
| `web_search.py` | Web search with fallback chain |
| `tools.py` | File and shell tool execution |
| `api.py` | Flask API for the web UI |
| `telegram_bot.py` | Telegram bot interface |
| `main.py` | Launcher — starts all components as threads |
| `ui/` | Web interface (React + Vite) |

---

## What Needs Help

### High priority
- **Docker / docker-compose** — currently macOS/Linux only via LaunchAgents/systemd
- **Windows compatibility** — untested; likely needs work in `tts.py`, `tools.py`, path handling
- **Automated tests** — nothing exists yet; any coverage is welcome
- **Long-run stability testing** — run for 7–30 days and report memory growth, CPU, DB size

### Medium priority
- **Additional messaging platforms** — Discord, Signal, WhatsApp
- **UI improvements** — model selector, API key management page, memory visualization
- **Better error messages** — many errors are developer-oriented; non-technical users need plain language

### Nice to have
- **Architecture diagrams**
- **Example screenshots / GIFs**
- **More cloud provider support** — Mistral, Cohere, etc.

---

## How to Submit Changes

1. Fork the repo
2. Create a branch: `git checkout -b your-feature-name`
3. Make your changes
4. Test it — at minimum run `./start.sh` and make sure nothing breaks
5. Open a pull request with a clear description of what changed and why

For bigger changes, open an issue first so we can discuss before you write a lot of code.

---

## Code Style

- Python: follow what's already there — `logging` not `print`, type hints on functions, docstrings on non-obvious things
- Keep components self-contained — each file should do one job
- If you touch the memory system, test it with multiple concurrent processes

---

## Security

If you find a security issue, please open a **private** GitHub issue rather than posting publicly. Shell execution (`VESPERA_ALLOW_SHELL`) and file access are the highest-risk areas.
