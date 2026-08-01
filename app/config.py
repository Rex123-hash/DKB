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


# ---------------------------------------------------------------------------
# PRIVATE OPERATOR SWITCH
# ---------------------------------------------------------------------------
# This is intentionally a source-code switch.  It is not exposed through the
# browser, an API route, a query parameter, or an environment variable.  Change
# it here and restart the server when you want DukanBook to use the configured
# Google Cloud providers.
GCP_ENABLED = False

# If a Google Cloud request fails while the private switch is enabled, the
# existing local providers may answer instead.  This is also code-only so an
# app user cannot change provider policy.
GCP_ALLOW_LOCAL_FALLBACK = True


def gcp_enabled() -> bool:
    """Return the private provider switch without consulting user input."""
    return GCP_ENABLED


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        return False


def has_llm() -> bool:
    return bool(
        (gcp_enabled() and os.environ.get("GOOGLE_CLOUD_PROJECT"))
        or os.environ.get("OLLAMA_MODEL")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GROQ_API_KEY")
    )


@lru_cache(maxsize=1)
def has_voice() -> bool:
    """Voice needs speech-to-text plus text-to-speech.

    STT is either Groq's hosted Whisper (a key, no install) or local
    faster-whisper. TTS is edge-tts, which needs no key.
    """
    if gcp_enabled() and os.environ.get("GOOGLE_CLOUD_PROJECT"):
        has_google_speech = _has_module("google.cloud.speech_v2")
        has_google_tts = _has_module("google.cloud.texttospeech")
        if has_google_speech and has_google_tts:
            return True
    has_tts = _has_module("edge_tts")
    has_stt = bool(os.environ.get("GROQ_API_KEY")) or (
        _has_module("faster_whisper")
    )
    return has_tts and has_stt
