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
    # Open without truncating — truncate AFTER acquiring lock so we never
    # wipe a running process's PID before confirming the lock is free.
    _lockfd = open(lock_file, 'a+')
    try:
        fcntl.flock(_lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        _lockfd.close()
        print("[VesperaTelegram] Already running. Exiting.")
        raise SystemExit(0)
    _lockfd.seek(0)
    _lockfd.truncate()
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

BOT_TOKEN          = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS      = os.getenv("TELEGRAM_ALLOWED_USERS", "")
VESPERA_API_TOKEN  = os.getenv("VESPERA_API_TOKEN", "")

# Auto-detect API port from .port file written by api.py on startup
def _get_api_url():
    port_file = Path(__file__).parent / ".port"
    if port_file.exists():
        try:
            port = int(port_file.read_text().strip())
            return f"http://localhost:{port}"
        except (ValueError, OSError):
            pass  # fall through to default
    return os.getenv("VESPERA_API_URL", "http://localhost:5055")

API_URL = _get_api_url()

logging.basicConfig(level=logging.INFO, format="[TelegramBot] %(message)s")
log = logging.getLogger(__name__)


def is_allowed(user_id: int) -> bool:
    # Re-read from env on every call so UI updates to TELEGRAM_ALLOWED_USERS
    # take effect without restarting the bot process. ALLOWED_USERS is frozen
    # at import time and cannot be used for the live check.
    try:
        from dotenv import load_dotenv as _ldenv
        _ldenv(Path(__file__).parent / ".env", override=True)
    except ImportError:
        pass
    live_users = [u.strip() for u in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if u.strip()]
    if not live_users:
        log.warning("TELEGRAM_ALLOWED_USERS not set — all users blocked. Add your ID to .env to use the bot.")
        return False
    return str(user_id) in live_users


def chat(message: str) -> dict:
    try:
        url = _get_api_url()
        # Re-read from env on every call so UI updates (or a first-time .env write)
        # take effect without restarting the bot process.
        try:
            from dotenv import load_dotenv as _ldenv
            _ldenv(Path(__file__).parent / ".env", override=True)
        except ImportError:
            pass
        _live_token = os.getenv("VESPERA_API_TOKEN", "")
        headers = {"Authorization": f"Bearer {_live_token}"} if _live_token else {}
        resp = None
        resp = requests.post(f"{url}/api/chat", json={"message": message, "tts": True}, headers=headers, timeout=60)
        try:
            resp.raise_for_status()
            return resp.json()
        finally:
            try:
                resp.close()
            except Exception:
                pass
    except Exception as e:
        return {"response": f"Error reaching Vespera API: {e}", "audio": None}


def send_reminder(reminder: dict, audio_path: str = None):
    """Called by scheduler when a reminder fires — sends to all allowed users."""
    import asyncio
    from telegram import Bot
    async def _send():
        bot = Bot(token=BOT_TOKEN)
        # Use same list as is_allowed() — single source of truth
        # Re-read from env — ALLOWED_USERS is frozen at import time; live updates
        # from the UI must be picked up here the same as in is_allowed().
        try:
            from dotenv import load_dotenv as _ldenv
            _ldenv(Path(__file__).parent / ".env", override=True)
        except ImportError:
            pass
        targets = [u.strip() for u in os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") if u.strip()]
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
        await update.message.reply_text(response)
        audio_url = result.get("audio")  # relative URL e.g. /api/audio/<hex>.mp3
        audio_bytes = None
        if audio_url:
            try:
                base = _get_api_url()
                # chat() already reloaded dotenv above; os.getenv picks up the fresh value.
                _live_token = os.getenv("VESPERA_API_TOKEN", "")
                headers = {"Authorization": f"Bearer {_live_token}"} if _live_token else {}
                r = None
                r = requests.get(f"{base}{audio_url}", headers=headers, timeout=15, stream=True)
                try:
                    r.raise_for_status()
                    # Cap at 5 MB — avoids loading an unexpectedly large file into RAM
                    _MAX_AUDIO_BYTES = 5 * 1024 * 1024
                    chunks = []
                    total = 0
                    for chunk in r.iter_content(chunk_size=8192):
                        total += len(chunk)
                        if total > _MAX_AUDIO_BYTES:
                            log.warning("TTS audio exceeds 5 MB — skipping voice message")
                            chunks = []
                            break
                        chunks.append(chunk)
                    audio_bytes = b"".join(chunks) if chunks else None
                finally:
                    try:
                        r.close()
                    except Exception:
                        pass
            except Exception as e:
                log.warning("Failed to fetch TTS audio: %s", e)

        if audio_bytes:
            try:
                await update.message.chat.send_action("upload_voice")
                import io
                await update.message.reply_voice(voice=io.BytesIO(audio_bytes))
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

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Save a photo sent via Telegram, with optional caption."""
        user_id = update.effective_user.id
        username = update.effective_user.username or str(user_id)
        if not is_allowed(user_id):
            log.warning(f"Blocked photo from: {username} ({user_id})")
            await update.message.reply_text("Access denied.")
            return

        caption = (update.message.caption or "").strip()
        log.info(f"Photo from {username}: caption={caption[:60]!r}")

        try:
            from photos import init_photos_db, add_photo, PHOTOS_DIR
            import uuid as _uuid
            init_photos_db()

            # Highest-resolution version is the last element
            photo = update.message.photo[-1]
            tg_file = await context.bot.get_file(photo.file_id)

            filename = f"{_uuid.uuid4().hex}.jpg"
            dest = PHOTOS_DIR / filename
            await tg_file.download_to_drive(str(dest))

            record = add_photo(filename, caption)
            short_id = record["id"][:8]
            caption_line = f"\nCaption: {caption}" if caption else ""
            await update.message.reply_text(
                f"\U0001f4f7 Photo saved!{caption_line}\nID: {short_id}\n\nSay \"my photos\" to list, or \"delete photo {short_id}\" to remove."
            )
        except Exception as e:
            log.error(f"Failed to save photo: {e}")
            await update.message.reply_text("Sorry, I couldn't save that photo. Please try again.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    log.info("Bot started — listening (text + photos).")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
