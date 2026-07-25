"""Voice I/O.

  * STT: Groq's hosted Whisper when GROQ_API_KEY is set, else faster-whisper
    (offline). Either way it handles Hindi / English / Hinglish.
  * TTS: edge-tts (no key, neural Indian voices; needs internet but no signup).

The hosted path exists because faster-whisper ships large native wheels and
downloads a model at runtime, which does not fit a small deployment container.

Heavy imports are lazy so the rest of the app starts fast and tests can
monkeypatch `transcribe` / `synthesize` without the libraries loaded.
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import os
import tempfile

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")  # tiny|base|small|medium

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")

# Neural edge-tts voices per language.
_VOICES = {
    "hi": "hi-IN-SwaraNeural",
    "en": "en-IN-NeerjaNeural",
}
_DEFAULT_VOICE = "hi-IN-SwaraNeural"  # also best for Hinglish

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel  # heavy, lazy
        _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _model


def _voice_for(lang: str | None) -> str:
    if not lang:
        return _DEFAULT_VOICE
    return _VOICES.get(lang.split("-")[0].lower(), _DEFAULT_VOICE)


# Hosted Whisper reports a language name ("hindi"); faster-whisper reports an
# ISO code ("hi"). Normalise so both paths feed `_voice_for` the same thing.
_LANG_NAMES = {"hindi": "hi", "english": "en", "urdu": "hi", "nepali": "hi"}


def _norm_lang(lang: str | None) -> str:
    if not lang:
        return "unknown"
    lang = lang.strip().lower()
    return _LANG_NAMES.get(lang, lang)


def _use_groq() -> bool:
    """Pick the speech-to-text backend.

    Local faster-whisper wins whenever it is installed, so a normal
    `python run.py` transcribes offline as before. The deployment container does
    not ship it (large native wheels + a runtime model download), so it falls
    through to Groq. Force either way with STT_BACKEND=local|cloud.
    """
    forced = os.environ.get("STT_BACKEND")
    if forced == "local":
        return False
    if forced == "cloud":
        return True
    if importlib.util.find_spec("faster_whisper") is not None:
        return False
    return bool(os.environ.get("GROQ_API_KEY"))


def _groq_transcribe(audio_bytes: bytes, filename: str) -> dict:
    """Speech bytes -> {'text', 'lang'} via Groq's hosted Whisper."""
    import requests  # lazy

    resp = requests.post(
        GROQ_STT_URL,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        files={"file": (filename or "audio.wav", io.BytesIO(audio_bytes))},
        data={"model": GROQ_STT_MODEL, "response_format": "verbose_json"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "text": (data.get("text") or "").strip(),
        "lang": _norm_lang(data.get("language")),
    }


def transcribe(audio_bytes: bytes, filename: str = "audio.wav") -> dict:
    """Speech bytes -> {'text', 'lang'}."""
    if _use_groq():
        return _groq_transcribe(audio_bytes, filename)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        segments, info = _get_model().transcribe(path, vad_filter=True)
        text = "".join(seg.text for seg in segments).strip()
        return {"text": text, "lang": getattr(info, "language", "unknown")}
    finally:
        os.remove(path)


async def _edge_synth(text: str, voice: str) -> bytes:
    import edge_tts  # lazy
    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


def synthesize(text: str, lang: str = "hi") -> bytes:
    """Text -> MP3 audio bytes (empty if text blank). No API key.

    Called from a worker thread (sync route), so asyncio.run is safe here.
    """
    if not text.strip():
        return b""
    return asyncio.run(_edge_synth(text, _voice_for(lang)))
