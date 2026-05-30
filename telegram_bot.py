"""
Vespera Telegram Bot
--------------------
Text Vespera from your phone and get responses.

Setup:
  1. Get a bot token from @BotFather on Telegram
  2. Add TELEGRAM_BOT_TOKEN=<token> to your .env
  3. Optional: set TELEGRAM_ALLOWED_USERS=your_telegram_user_id
     to restrict access to only you (recommended)
"""

import os
import signal
import atexit
import logging
import requests
from pathlib import Path
from security import ALLOWED_TELEGRAM_USERS as _SECURITY_ALLOWED_USERS

# ── PID lock: one bot instance only ──────────────────────────────────────────
_pid_file = Path(__file__).parent / ".telegram.pid"

def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False

if _pid_file.exists():
    try:
        _existing = int(_pid_file.read_text().strip())
        if _pid_running(_existing):
            print(f"[VesperaTelegram] Already running (PID {_existing}). Exiting.")
            raise SystemExit(0)
    except ValueError:
        pass

_pid_file.write_text(str(os.getpid()))
atexit.register(lambda: _pid_file.unlink(missing_ok=True))
def _handle_sigterm(*_):
    raise SystemExit(0)
signal.signal(signal.SIGTERM, _handle_sigterm)
# ─────────────────────────────────────────────────────────────────────────────

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

BOT_TOKEN     = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS = os.getenv("TELEGRAM_ALLOWED_USERS", "")

# Auto-detect API port from .port file written by api.py on startup
def _get_api_url():
    port_file = Path(__file__).parent / ".port"
    if port_file.exists():
        return f"http://localhost:{port_file.read_text().strip()}"
    return os.getenv("VESPERA_API_URL", "http://localhost:5055")

API_URL = _get_api_url()

logging.basicConfig(level=logging.INFO, format="[TelegramBot] %(message)s")
log = logging.getLogger(__name__)


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    return str(user_id) in [u.strip() for u in ALLOWED_USERS.split(",")]


def chat(message: str) -> dict:
    try:
        resp = requests.post(f"{API_URL}/api/chat", json={"message": message, "tts": True}, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"response": f"Error reaching Vespera API: {e}", "audio": None}


def send_reminder(reminder: dict, audio_path: str = None):
    """Called by scheduler when a reminder fires — sends to all allowed users."""
    import asyncio
    from telegram import Bot
    async def _send():
        bot = Bot(token=BOT_TOKEN)
        targets = _SECURITY_ALLOWED_USERS if _SECURITY_ALLOWED_USERS else []
        for uid in targets:
            try:
                await bot.send_message(chat_id=int(uid), text=f"🔔 Reminder: {reminder['message']}")
                if audio_path:
                    with open(audio_path, "rb") as f:
                        await bot.send_voice(chat_id=int(uid), voice=f)
            except Exception as e:
                log.warning(f"Reminder delivery failed for {uid}: {e}")
    try:
        asyncio.run(_send())
    except Exception as e:
        log.warning(f"Reminder send error: {e}")


def run():
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN not set in .env — bot not started.")
        return

    from telegram import Update
    from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

    async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        username = update.effective_user.username or str(user_id)
        if not is_allowed(user_id):
            log.warning(f"Blocked: {username} ({user_id})")
            await update.message.reply_text("Access denied.")
            return
        text = update.message.text or ""
        log.info(f"From {username}: {text[:60]}")
        await update.message.chat.send_action("typing")
        result = chat(text)
        response = result.get("response", "(no response)")
        audio = result.get("audio")
        await update.message.reply_text(response)
        if audio:
            try:
                await update.message.chat.send_action("upload_voice")
                with open(audio, "rb") as f:
                    await update.message.reply_voice(voice=f)
            except Exception as e:
                log.warning(f"Voice send failed: {e}")

    # Register reminder delivery callback
    from scheduler import register_callback
    register_callback(send_reminder)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started — listening.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
