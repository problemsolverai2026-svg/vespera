# Vespera

A lightweight persistent AI memory system. Runs locally 24/7.

Your AI stays warm between conversations — it remembers you, thinks in the background, and only calls an expensive cloud model when it actually needs to.

---

## How It Works

Four components run in parallel:

| Component | What it does |
|---|---|
| **Background Loop** | Lightly reviews past conversations, generates brief thoughts, saves to memory |
| **Cleanup Crew** | Reviews fresh thoughts every 5 minutes — keeps good ones, prunes garbage |
| **Periodic Pruning** | Deep clean every 3 days — promotes the best memories to permanent core |
| **Handoff Logic** | Decides if your message is handled locally or by a cloud model |

### Memory Layers (nesting doll)

```
working → recent → validated → core
```

- **working** — active conversation context
- **recent** — fresh background thoughts, not yet reviewed
- **validated** — cleanup crew approved
- **core** — permanent, trusted memories (never auto-pruned)

Memories can be linked to each other — related, expands, contradicts, references.

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.ai) running locally
- A local model pulled (recommended: `qwen2.5:7b` or `llama3.1:8b`)

---

## Setup

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/vespera.git
cd vespera

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your API keys and model preferences

# 4. Pull a local model (if you haven't already)
ollama pull qwen2.5:7b

# 5. Run
python3 main.py
```

---

## Configuration

Edit `.env` to set your preferences:

```env
OLLAMA_MODEL=qwen2.5:7b          # local model (7B recommended)
CLOUD_API_KEY=your_key_here      # only needed for cloud handoff
VENICE_API_KEY=your_key_here     # for web search (free tier)
COMPLEXITY_THRESHOLD=0.65        # above this → cloud handles it
```

---

## Test It

```bash
python3 main.py --test
```

Runs one pass of each component and exits. Good for verifying everything works before running full time.

---

## Project Structure

```
vespera/
├── main.py              # launcher — runs all components
├── background_loop.py   # persistent thinking engine
├── cleanup_crew.py      # frequent memory review
├── periodic_pruning.py  # deep clean every 3 days
├── handoff.py           # local vs cloud decision logic
├── config.py            # all settings
├── memory/
│   ├── store.py         # memory read/write API
│   ├── schema.sql       # database schema
│   └── __init__.py
├── .env.example         # config template
├── requirements.txt
└── .gitignore
```

---

## Philosophy

The LLM is the DNA — foundational knowledge baked in. Everything else is built around it to give it continuity and persistence.

Each user runs their own instance with their own personality and memory. Nothing is shared unless you choose to share it.

---

## Status

Early build. Core memory system is functional. Cloud handoff is wired but needs your API key. Local model quality improves significantly with a 7B+ model.
