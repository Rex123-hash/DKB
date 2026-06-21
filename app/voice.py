"""Voice I/O using local Python libraries — NO API keys.

  * STT: faster-whisper (offline Whisper). Handles Hindi / English / Hinglish.
  * TTS: edge-tts (no key, neural Indian voices; needs internet but no signup).

Both heavy imports are lazy so the rest of the app starts fast and tests can
monkeypatch `transcribe` / `synthesize` without the libraries loaded.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")  # tiny|base|small|medium

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


def transcribe(audio_bytes: bytes, filename: str = "audio.wav") -> dict:
    """Speech bytes -> {'text', 'lang'} fully offline."""
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
