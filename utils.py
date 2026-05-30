"""
Vespera Utilities
-----------------
Shared helpers used across components.
"""

import logging
import os
from pathlib import Path


def get_logger(name: str) -> logging.Logger:
    """
    Return a module-level logger with consistent format.
    Level controlled by VESPERA_LOG_LEVEL env var (default INFO).
    """
    level = os.getenv("VESPERA_LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(name)s] %(levelname)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level, logging.INFO))
    return logger

import json
import re


def parse_json_response(raw: str) -> dict | None:
    """
    Robustly extract a JSON object from a model response.

    Handles:
    - Plain JSON: {"key": "value"}
    - Markdown fences: ```json\\n{...}\\n```
    - JSON buried in prose: "Here is the result: {...} Hope that helps!"
    - Nested braces inside strings (uses last } not first)

    Returns None if no valid JSON object found.
    """
    if not raw:
        return None

    # 1. Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. Try the whole string as-is
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # 3. Find the outermost { ... } span using a bracket counter
    start = raw.find("{")
    if start == -1:
        return None

    depth = 0
    end = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(raw[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end == -1:
        return None

    try:
        return json.loads(raw[start:end])
    except json.JSONDecodeError:
        return None
