"""
Centralized .env loader for Vespera.
Import this at the top of any file instead of repeating the try/except block.
"""
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass
