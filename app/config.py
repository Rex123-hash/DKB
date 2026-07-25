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
    """Voice needs speech-to-text plus text-to-speech.

    STT is either Groq's hosted Whisper (a key, no install) or local
    faster-whisper. TTS is edge-tts, which needs no key.
    """
    has_tts = importlib.util.find_spec("edge_tts") is not None
    has_stt = bool(os.environ.get("GROQ_API_KEY")) or (
        importlib.util.find_spec("faster_whisper") is not None
    )
    return has_tts and has_stt
