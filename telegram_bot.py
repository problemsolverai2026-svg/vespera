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

def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _acquire_pid_lock() -> None:
    """Ensure only one bot instance runs. Uses flock — atomic and SIGKILL-safe."""
    import fcntl
    lock_file = Path(__file__).parent / ".telegram.lock"
    global _lockfd
    _lockfd = open(lock_file, 'w')
    try:
        fcntl.flock(_lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("[VesperaTelegram] Already running. Exiting.")
        raise SystemExit(0)
    _lockfd.write(str(os.getpid()))
    _lockfd.flush()
    def _handle_sigterm(*_): raise SystemExit(0)
    signal.signal(signal.SIGTERM, _handle_sigterm)

_lockfd = None  # module-level ref keeps fd open (and lock held) for process lifetime

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
    # Default DENY if no allowlist is configured — bot should never be open to strangers
    if not ALLOWED_USERS:
        log.warning("TELEGRAM_ALLOWED_USERS not set — all users blocked. Add your ID to .env to use the bot.")
        return False
    return str(user_id) in [u.strip() for u in ALLOWED_USERS.split(",")]


def chat(message: str) -> dict:
    try:
        url = _get_api_url()
        resp = requests.post(f"{url}/api/chat", json={"message": message, "tts": True}, timeout=60)
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
        # Use same list as is_allowed() — single source of truth
        targets = [u.strip() for u in ALLOWED_USERS.split(",") if u.strip()] if ALLOWED_USERS else []
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
    _acquire_pid_lock()
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
        audio = result.get("audio_path")  # local path for direct file read
        await update.message.reply_text(response)
        if audio:
            try:
                await update.message.chat.send_action("upload_voice")
                with open(audio, "rb") as f:
                    await update.message.reply_voice(voice=f)
            except Exception as e:
                log.warning(f"Voice send failed: {e}")

    # Register reminder delivery callback.
    # Only start the scheduler here if main.py is NOT already running
    # (avoids duplicate scheduler when both processes are up together).
    import threading
    from scheduler import register_callback, run as scheduler_run
    register_callback(send_reminder)

    def _main_running() -> bool:
        import fcntl
        lock_file = Path(__file__).parent / ".main.lock"
        if not lock_file.exists():
            return False
        fd = None
        try:
            fd = open(lock_file, 'r')  # 'r' — does NOT truncate content
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False  # got lock = nobody holds it = main not running
        except IOError:
            return True  # lock held = main.py is running
        finally:
            if fd is not None:
                fd.close()

    if not _main_running():
        _sched_shutdown = threading.Event()
        _sched_thread = threading.Thread(
            target=scheduler_run, args=(_sched_shutdown,), daemon=True, name="Scheduler"
        )
        _sched_thread.start()
        log.info("Scheduler started in background thread (main.py not running).")
    else:
        log.info("main.py is running — skipping local scheduler to avoid duplicates.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info("Bot started — listening.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
