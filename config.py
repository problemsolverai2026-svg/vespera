"""
Vespera Configuration
---------------------
All settings in one place. Loads from .env if present.
Copy .env.example to .env and fill in your values.

Each component has its own model assignment so you can tune
cost vs quality per role. Cheap fast model for cleanup,
smarter model for handoff decisions, etc.
"""

import os
from pathlib import Path

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────

BASE_DIR       = Path(__file__).parent
MEMORY_DB_PATH = BASE_DIR / "memory" / "vespera.db"

# ─────────────────────────────────────────────
# COMPONENT MODEL ASSIGNMENTS
# Each component can run a different model.
# Set these in .env to override defaults.
# ─────────────────────────────────────────────

COMPONENTS = {

    "background_loop": {
        "description": "Persistent thinking engine. Runs 24/7 in the background, lightly reviewing past conversations and generating brief thoughts. Saves output to the 'recent' memory layer.",
        "role": "local",
        "ollama_url":   os.getenv("BACKGROUND_OLLAMA_URL",   "http://localhost:11434/api/generate"),
        "ollama_model": os.getenv("BACKGROUND_OLLAMA_MODEL", "llama3.2:3b"),
        "api_key":      os.getenv("BACKGROUND_API_KEY",      ""),
    },

    "cleanup_crew": {
        "description": "First-pass memory reviewer. Runs every 5 minutes, checks recent thoughts, promotes good ones to 'validated' and prunes garbage. Keeps the memory layer clean and stable.",
        "role": "local",
        "ollama_url":   os.getenv("CLEANUP_OLLAMA_URL",   "http://localhost:11434/api/generate"),
        "ollama_model": os.getenv("CLEANUP_OLLAMA_MODEL", "llama3.2:3b"),
        "api_key":      os.getenv("CLEANUP_API_KEY",      ""),
    },

    "periodic_pruning": {
        "description": "Deep memory reviewer. Runs every 3 days, applies stricter criteria than the cleanup crew. Promotes the best memories to permanent 'core' storage and removes anything outdated or redundant.",
        "role": "local",
        "ollama_url":   os.getenv("PRUNING_OLLAMA_URL",   "http://localhost:11434/api/generate"),
        "ollama_model": os.getenv("PRUNING_OLLAMA_MODEL", "llama3.2:3b"),
        "api_key":      os.getenv("PRUNING_API_KEY",      ""),
    },

    "handoff": {
        "description": "Conversation router. Scores each user message for complexity and decides whether the local model handles it or passes it to a cloud model. Also scores the local model's confidence before responding.",
        "role": "local",
        "ollama_url":   os.getenv("HANDOFF_OLLAMA_URL",   "http://localhost:11434/api/generate"),
        "ollama_model": os.getenv("HANDOFF_OLLAMA_MODEL", "llama3.2:3b"),
        "api_key":      os.getenv("HANDOFF_API_KEY",      ""),
    },

    "cloud": {
        "description": "Cloud model. Only called when the handoff logic decides a message is too complex for the local model. This is where your best AI goes — Claude, Grok, GPT, Venice, etc. Costs money per call.",
        "role": "cloud",
        "provider":     os.getenv("CLOUD_PROVIDER",    "claude"),
        "model":        os.getenv("CLOUD_MODEL",       "claude-sonnet-4-5"),
        "api_key":      os.getenv("CLOUD_API_KEY",     ""),
        "base_url":     os.getenv("CLOUD_BASE_URL",    ""),
    },

}

# ─────────────────────────────────────────────
# SHORTHAND ACCESSORS (used by components)
# ─────────────────────────────────────────────

def get_component(name: str) -> dict:
    if name not in COMPONENTS:
        raise ValueError(f"Unknown component: {name}")
    return COMPONENTS[name]

# ─────────────────────────────────────────────
# WEB SEARCH
# ─────────────────────────────────────────────

VENICE_API_KEY    = os.getenv("VENICE_API_KEY",    "")
VENICE_SEARCH_URL = "https://api.venice.ai/api/v1/augment/search"

# ─────────────────────────────────────────────
# TIMING
# ─────────────────────────────────────────────

BACKGROUND_LOOP_INTERVAL = int(os.getenv("BACKGROUND_LOOP_INTERVAL", "180"))
CLEANUP_INTERVAL         = int(os.getenv("CLEANUP_INTERVAL",         "300"))
PRUNING_INTERVAL_DAYS    = int(os.getenv("PRUNING_INTERVAL_DAYS",    "3"))

# ─────────────────────────────────────────────
# TUNING
# ─────────────────────────────────────────────

COMPLEXITY_THRESHOLD = float(os.getenv("COMPLEXITY_THRESHOLD", "0.65"))
MAX_THOUGHT_LENGTH   = int(os.getenv("MAX_THOUGHT_LENGTH",     "300"))
CLEANUP_BATCH_SIZE   = int(os.getenv("CLEANUP_BATCH_SIZE",     "5"))
PRUNING_BATCH_SIZE   = int(os.getenv("PRUNING_BATCH_SIZE",     "20"))
