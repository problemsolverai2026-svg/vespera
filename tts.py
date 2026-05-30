"""
Vespera TTS
-----------
Layered voice output. Works for everyone — local or cloud.

Priority:
  1. Venice     — VENICE_API_KEY set (cloud, best quality)
  2. edge-tts   — free, Microsoft servers, good quality
  3. kokoro-onnx — fully local, good quality, auto-downloads ~80MB on first use
  4. pyttsx3    — offline fallback, no download, works everywhere

Models are cached in ~/.vespera/models/ after first download.
"""

import os
import uuid
import asyncio
import threading
import requests
from pathlib import Path
from utils import get_logger

log = get_logger("tts")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

VENICE_API_KEY = os.getenv("VENICE_API_KEY", "")
VENICE_TTS_URL = "https://api.venice.ai/api/v1/audio/speech"
VENICE_MODEL   = os.getenv("TTS_MODEL", "tts-kokoro")
VENICE_VOICE   = os.getenv("TTS_VOICE", "am_michael")
EDGE_VOICE     = os.getenv("EDGE_TTS_VOICE", "en-US-GuyNeural")
KOKORO_VOICE   = os.getenv("KOKORO_VOICE", "af_heart")

TTS_DIR    = Path("/tmp/vespera-tts")
MODELS_DIR = Path.home() / ".vespera" / "models"
TTS_DIR.mkdir(exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

KOKORO_MODEL_URL  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
KOKORO_MODEL_PATH  = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES_PATH = MODELS_DIR / "voices-v1.0.bin"


# ─────────────────────────────────────────────
# PROVIDERS
# ─────────────────────────────────────────────

def _tts_venice(text: str) -> str | None:
    try:
        resp = requests.post(
            VENICE_TTS_URL,
            headers={"Authorization": f"Bearer {VENICE_API_KEY}"},
            json={"model": VENICE_MODEL, "input": text, "voice": VENICE_VOICE},
            timeout=30,
        )
        resp.raise_for_status()
        out = TTS_DIR / f"{uuid.uuid4().hex}.mp3"
        out.write_bytes(resp.content)
        log.debug("Venice → %s", out.name)
        return str(out)
    except Exception as e:
        log.error("Venice error: %s", e)
        return None


def _tts_edge(text: str) -> str | None:
    try:
        import edge_tts
        out = TTS_DIR / f"{uuid.uuid4().hex}.mp3"
        async def _run():
            c = edge_tts.Communicate(text, EDGE_VOICE)
            await c.save(str(out))
        asyncio.run(_run())
        log.debug("edge-tts → %s", out.name)
        return str(out)
    except Exception as e:
        log.error("edge-tts error: %s", e)
        return None


_kokoro_ready = threading.Event()
_kokoro_lock  = threading.Lock()


def _download_kokoro_bg():
    """Download kokoro model files in a background thread."""
    with _kokoro_lock:
        if KOKORO_MODEL_PATH.exists() and KOKORO_VOICES_PATH.exists():
            _kokoro_ready.set()
            return
        log.info("Kokoro model not found — downloading in background (~80MB, one time only)...")
        try:
            tmp_files = []
            for url, path in [(KOKORO_MODEL_URL, KOKORO_MODEL_PATH), (KOKORO_VOICES_URL, KOKORO_VOICES_PATH)]:
                tmp = path.with_suffix('.tmp')
                log.info("Downloading %s...", path.name)
                resp = requests.get(url, stream=True, timeout=120)
                resp.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                tmp_files.append((tmp, path))
            # Only rename after BOTH downloads succeed — prevents partial-file false-ready
            for tmp, path in tmp_files:
                tmp.rename(path)
            log.info("Kokoro download complete.")
            _kokoro_ready.set()
        except Exception as e:
            log.error("Kokoro download failed: %s", e)
            # Clean up any partial .tmp files so next startup retries cleanly
            for url, path in [(KOKORO_MODEL_URL, KOKORO_MODEL_PATH), (KOKORO_VOICES_URL, KOKORO_VOICES_PATH)]:
                try:
                    path.with_suffix('.tmp').unlink(missing_ok=True)
                except Exception:
                    pass
            # Leave _kokoro_ready unset so _tts_kokoro skips gracefully


# Kick off background download at import time if files are missing
if KOKORO_MODEL_PATH.exists() and KOKORO_VOICES_PATH.exists():
    _kokoro_ready.set()
else:
    threading.Thread(target=_download_kokoro_bg, daemon=True, name="kokoro-dl").start()


def _tts_kokoro(text: str) -> str | None:
    try:
        if not _kokoro_ready.is_set():
            log.debug("Kokoro not ready yet (downloading) — skipping")
            return None
        from kokoro_onnx import Kokoro
        import soundfile as sf
        import numpy as np
        kokoro = Kokoro(str(KOKORO_MODEL_PATH), str(KOKORO_VOICES_PATH))
        samples, sr = kokoro.create(text, voice=KOKORO_VOICE, speed=1.0, lang="en-us")
        out = TTS_DIR / f"{uuid.uuid4().hex}.wav"
        sf.write(str(out), samples, sr)
        log.debug("kokoro-onnx → %s", out.name)
        return str(out)
    except Exception as e:
        log.error("kokoro-onnx error: %s", e)
        return None


def _tts_pyttsx3(text: str) -> str | None:
    try:
        import pyttsx3
        import tempfile
        engine = pyttsx3.init()
        out = TTS_DIR / f"{uuid.uuid4().hex}.wav"
        engine.save_to_file(text, str(out))
        engine.runAndWait()
        log.debug("pyttsx3 → %s", out.name)
        return str(out)
    except Exception as e:
        log.error("pyttsx3 error: %s", e)
        return None


# ─────────────────────────────────────────────
# MAIN ENTRY
# ─────────────────────────────────────────────

def _cleanup_tts_dir(max_age_seconds: int = 3600):
    """Delete TTS files older than max_age_seconds (default 1 hour)."""
    import time
    now = time.time()
    for f in TTS_DIR.iterdir():
        try:
            if f.is_file() and (now - f.stat().st_mtime) > max_age_seconds:
                f.unlink()
        except Exception:
            pass


def speak(text: str) -> str | None:
    if not text or not text.strip():
        return None
    if len(text) > 1500:
        text = text[:1500] + "..."
    _cleanup_tts_dir()

    if VENICE_API_KEY:
        result = _tts_venice(text)
        if result:
            return result

    result = _tts_edge(text)
    if result:
        return result

    result = _tts_kokoro(text)
    if result:
        return result

    return _tts_pyttsx3(text)


if __name__ == "__main__":
    path = speak("Vespera TTS is working. Hello from your local AI assistant.")
    log.info("Output: %s", path)
