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

# Shell execution — on by default, set false to disable
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

# Max tokens per cloud response — cost control
MAX_TOKENS = int(os.getenv("VESPERA_MAX_TOKENS", "1024"))

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
    return hmac.compare_digest(request_token.encode(), API_TOKEN.encode())


def get_status() -> dict:
    """Return current security config (safe to display in UI — no secrets)."""
    return {
        "shell_execution": ALLOW_SHELL,
        "allowed_paths": ALLOW_PATHS,
        "api_token_required": bool(API_TOKEN),
        "max_tokens": MAX_TOKENS,
        "telegram_restricted": bool(ALLOWED_TELEGRAM_USERS),
        "telegram_allowed_count": len(ALLOWED_TELEGRAM_USERS),
    }
