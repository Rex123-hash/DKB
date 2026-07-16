"""Loads environment config from a .env file (if present).

Put your key in a file named `.env` at the project root:

    GROQ_API_KEY=gsk_...
    # optional: GROQ_MODEL=llama-3.3-70b-versatile

This module is imported early (by app.main and app.brain) so the key is
available to the LLM brain without exporting it manually each time.
"""
from __future__ import annotations

import importlib.util
import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()  # reads .env from cwd / project root if it exists


def has_llm() -> bool:
    return bool(
        os.environ.get("OLLAMA_MODEL")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GROQ_API_KEY")
    )


@lru_cache(maxsize=1)
def has_voice() -> bool:
    """Voice is available if the local libraries are installed (no key needed)."""
    return all(
        importlib.util.find_spec(m) is not None
        for m in ("faster_whisper", "edge_tts")
    )
