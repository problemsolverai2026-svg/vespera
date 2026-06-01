"""
Vespera Security
----------------
All security settings in one place.

Configure via .env:
  VESPERA_ALLOW_SHELL=true/false     — enable/disable shell command execution
  VESPERA_ALLOW_PATHS=~/,/tmp        — comma-separated allowed file paths
  VESPERA_API_TOKEN=yourtoken        — require token for all API requests (optional)
  VESPERA_MAX_TOKENS=1024            — max tokens per cloud model response
  TELEGRAM_ALLOWED_USERS=id1,id2     — restrict Telegram access to these user IDs
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

HOME = str(Path.home())

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────

# Shell execution — off by default, set VESPERA_ALLOW_SHELL=true to enable
ALLOW_SHELL = os.getenv("VESPERA_ALLOW_SHELL", "false").lower() == "true"

# Allowed file paths — defaults to home directory
_DEFAULT_ALLOW_PATH = str(Path.home() / ".vespera" / "workspace")
_raw_paths  = os.getenv("VESPERA_ALLOW_PATHS", _DEFAULT_ALLOW_PATH)
ALLOW_PATHS = [p.strip().replace("~", HOME) for p in _raw_paths.split(",")]

# Ensure configured paths exist so file tools don't fail silently on first run
for _p in ALLOW_PATHS:
    try:
        Path(_p).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# API auth token — if set, all API requests must include header:
# Authorization: Bearer <token>
API_TOKEN = os.getenv("VESPERA_API_TOKEN", "")
if not API_TOKEN:
    print(
        "\n[Vespera] ⚠️  WARNING: VESPERA_API_TOKEN is not set. "
        "The API is open to anyone who can reach it. "
        "Set VESPERA_API_TOKEN in your .env to require authentication.\n"
    )

# Max tokens per cloud response — cost control
def _safe_int(env_key: str, default: int) -> int:
    val = os.getenv(env_key, str(default))
    try:
        return int(val)
    except ValueError:
        print(f"[security] WARNING: {env_key}='{val}' is not a valid integer — using default {default}")
        return default

MAX_TOKENS = _safe_int("VESPERA_MAX_TOKENS", 1024)

# Telegram allowed user IDs
_raw_users = os.getenv("TELEGRAM_ALLOWED_USERS", "")
ALLOWED_TELEGRAM_USERS = [u.strip() for u in _raw_users.split(",") if u.strip()]


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def path_allowed(path: str) -> bool:
    """Check if a file path is within allowed directories.
    Uses proper path comparison to prevent traversal bypass via startswith.
    """
    resolved = Path(path.replace("~", HOME)).resolve()
    for allowed in ALLOW_PATHS:
        allowed_path = Path(allowed).resolve()
        try:
            resolved.relative_to(allowed_path)  # raises ValueError if not under allowed_path
            return True
        except ValueError:
            continue
    return False


def telegram_user_allowed(user_id: int) -> bool:
    """Check if a Telegram user ID is allowed. Defaults to DENY if no list set."""
    if not ALLOWED_TELEGRAM_USERS:
        return False
    return str(user_id) in ALLOWED_TELEGRAM_USERS


def check_api_token(request_token: str) -> bool:
    """Validate API token. Returns True if no token required or token matches."""
    if not API_TOKEN:
        return True
    import hmac
    return hmac.compare_digest(request_token, API_TOKEN)


def get_status() -> dict:
    """Return current security config (safe to display in UI — no secrets)."""
    return {
        "shell_execution": ALLOW_SHELL,
        "allowed_paths": ALLOW_PATHS,
        "api_token_required": bool(API_TOKEN),
        "max_tokens": MAX_TOKENS,
        "telegram_restricted": bool(ALLOWED_TELEGRAM_USERS),
        "telegram_allowed_count": len(ALLOWED_TELEGRAM_USERS),
        "telegram_allowed_users": list(ALLOWED_TELEGRAM_USERS),
    }
