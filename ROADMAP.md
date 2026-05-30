# Vespera Build Roadmap
_Last updated: 2026-05-28_

## Goal
Self-hosted, open-source AI assistant. Own your data, own your hardware, no subscription.
Just an Anthropic (or Groq/Venice) API key and a machine to run it on.

---

## ✅ Built & Working

### Core
- Memory store (SQLite, nesting doll: working → recent → validated → core)
- Background loop (local model thinks every 3 min, saves to memory)
- Cleanup crew (promotes/prunes recent memories every 5 min)
- Periodic pruning (deep clean every 3 days, validated → core)
- Handoff logic (local model → cloud for complex queries)
- Flask API (port 5055, auto-increments if taken)
- Scheduler / reminders (natural language, fires to Telegram + TTS)

### Integrations
- Web search — DuckDuckGo (default, no key), Brave (free key), Venice (optional)
- Tool execution — shell commands, file read/write (via cloud model)
- TTS — Venice (primary), edge-tts (free), kokoro-onnx (local, auto-downloads), pyttsx3 fallback
- Telegram bot — text from phone, voice replies, allowed user list
- Chat UI — Lovable frontend (localhost:3055)

### Infrastructure
- LaunchAgents (macOS) — all 4 components auto-start on boot
- systemd services (Linux) — equivalent auto-start files included
- vespera-start / vespera-stop commands
- start.sh — one command launches everything
- Port auto-detection — no conflicts if ports are in use
- Security module — central config for shell toggle, path limits, API token, Telegram allowlist

---

## 🔨 Still To Build (Post v1)

### Phase 1 — UI Polish
- [ ] Web UI TTS playback — browser plays audio response automatically
- [ ] Web UI password protection
- [ ] Model selector in UI — pick from downloaded Ollama models per component
- [ ] Resources page in UI — links to every API with pricing
- [x] Contributing guide
- [ ] docker-compose.yml for one-command deploy

### Phase 2 — Messaging
- [ ] Discord bot
- [ ] Signal messaging
- [ ] WhatsApp (third-party library)
- [ ] iMessage (macOS only)

### Phase 3 — Advanced
- [ ] Browser control (CDP integration)
- [ ] Tailscale setup guide for remote access
- [ ] Windows support

---

## Model Recommendations

| RAM | Recommended Model | Notes |
|-----|-------------------|-------|
| 8GB | mistral:7b | Lightweight, works well |
| 16GB | qwen2.5:14b | Best quality that fits comfortably |
| 32GB+ | qwen2.5:32b | Excellent reasoning and JSON output |

---

## What Replaced OpenClaw (for personal use)
- Telegram bot → messaging from anywhere
- Tool execution → run commands, read/write files
- Web search → current information without API key
- TTS → voice responses
- Scheduler → reminders
- Memory → persistent context across sessions
