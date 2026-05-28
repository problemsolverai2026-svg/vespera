"""
Vespera Configuration
---------------------
All settings in one place. Uses environment variables for secrets.
Copy .env.example to .env and fill in your values.
"""

import os
from pathlib import Path

# ─────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
MEMORY_DB_PATH = BASE_DIR / "memory" / "vespera.db"

# ─────────────────────────────────────────────
# LOCAL MODEL
# ─────────────────────────────────────────────

OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")   # recommend: qwen2.5:7b

# ─────────────────────────────────────────────
# CLOUD MODEL (only called when local hands off)
# ─────────────────────────────────────────────

CLOUD_PROVIDER = os.getenv("CLOUD_PROVIDER", "claude")    # claude | grok | venice
CLOUD_MODEL    = os.getenv("CLOUD_MODEL", "claude-sonnet-4-5")
CLOUD_API_KEY  = os.getenv("CLOUD_API_KEY", "")

# ─────────────────────────────────────────────
# WEB SEARCH (Venice AI — free tier)
# ─────────────────────────────────────────────

VENICE_API_KEY    = os.getenv("VENICE_API_KEY", "")
VENICE_SEARCH_URL = "https://api.venice.ai/api/v1/augment/search"

# ─────────────────────────────────────────────
# TIMING
# ─────────────────────────────────────────────

BACKGROUND_LOOP_INTERVAL = int(os.getenv("BACKGROUND_LOOP_INTERVAL", "180"))   # seconds
CLEANUP_INTERVAL         = int(os.getenv("CLEANUP_INTERVAL", "300"))            # seconds
PRUNING_INTERVAL_DAYS    = int(os.getenv("PRUNING_INTERVAL_DAYS", "3"))

# ─────────────────────────────────────────────
# TUNING
# ─────────────────────────────────────────────

COMPLEXITY_THRESHOLD = float(os.getenv("COMPLEXITY_THRESHOLD", "0.65"))
MAX_THOUGHT_LENGTH   = int(os.getenv("MAX_THOUGHT_LENGTH", "300"))
CLEANUP_BATCH_SIZE   = int(os.getenv("CLEANUP_BATCH_SIZE", "5"))
PRUNING_BATCH_SIZE   = int(os.getenv("PRUNING_BATCH_SIZE", "20"))
