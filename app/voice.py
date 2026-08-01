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
import re
import tempfile

from app import config

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")  # tiny|base|small|medium

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# large-v3 is noticeably more accurate than the turbo variant on Hindi and
# code-mixed speech; the extra latency is worth it for short shop commands.
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")
# Telling Whisper the language beats letting it auto-detect, which flips
# between Hindi and English on Hinglish and garbles both.
STT_LANGUAGE = os.environ.get("STT_LANGUAGE", "hi")

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


# The words a shopkeeper actually uses, spelled the way we want them back.
# Whisper conditions on this text, so it stops inventing spellings for common
# ledger terms and hears Hinglish as Hinglish.
LEDGER_VOCAB = (
    "udhaar, jama, khaata, baaki, balance, payment, reminder, call karna, "
    "phone number, credit, debit, rupaye, sau, hazaar, lakh, "
    "customer, supplier, saman, stock, bill, GST"
)


def build_hint(names: list[str] | None = None) -> str:
    """Vocabulary hint for the recogniser.

    Written as example Hinglish sentences using the shop's own party names,
    because Whisper mirrors the script and phrasing of its prompt: given
    Devanagari it answers in Devanagari, given Roman Hinglish it answers in
    Roman Hinglish and spells the names the way the ledger stores them.
    """
    names = [n.strip() for n in (names or []) if n and n.strip()]
    a = names[0] if names else "Ramesh"
    b = names[1] if len(names) > 1 else "Suresh"
    c = names[2] if len(names) > 2 else "Verma Traders"
    examples = (
        f"{a} ko paanch sau ka udhaar likho. "
        f"{b} ne do hazaar jama kiye. "
        f"{c} ka khaata banao aur payment ka reminder do. "
        f"{a} ka balance kitna hai?"
    )
    roster = f" Names: {', '.join(names[:20])}." if names else ""
    return f"{examples}{roster} Words: {LEDGER_VOCAB}."


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


def _groq_transcribe(audio_bytes: bytes, filename: str, hint: str = "") -> dict:
    """Speech bytes -> {'text', 'lang'} via Groq's hosted Whisper.

    `hint` biases the decoder towards words it should expect — the shop's own
    party names and the ledger vocabulary — which is what stops "Ramesh" coming
    back as "दमेश".
    """
    import requests  # lazy

    data = {
        "model": GROQ_STT_MODEL,
        "response_format": "verbose_json",
        "temperature": "0",  # deterministic; no creative re-interpretation
    }
    if STT_LANGUAGE:
        data["language"] = STT_LANGUAGE
    if hint:
        data["prompt"] = hint[:880]  # Whisper only conditions on ~224 tokens
    resp = requests.post(
        GROQ_STT_URL,
        headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"},
        files={"file": (filename or "audio.wav", io.BytesIO(audio_bytes))},
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "text": (data.get("text") or "").strip(),
        "lang": _norm_lang(data.get("language")),
    }


def _gcp_transcribe(audio_bytes: bytes, hint: str = "") -> dict:
    """Speech bytes -> text through Speech-to-Text V2 Chirp 3."""
    from google.api_core.client_options import ClientOptions
    from google.cloud.speech_v2 import SpeechClient
    from google.cloud.speech_v2.types import cloud_speech

    from app import gcp

    location = os.environ.get("GCP_STT_LOCATION", "global").strip() or "global"
    client_options = None
    if location != "global":
        client_options = ClientOptions(api_endpoint=f"{location}-speech.googleapis.com")
    client = SpeechClient(client_options=client_options)
    language_codes = [
        code.strip()
        for code in os.environ.get("GCP_STT_LANGUAGE_CODES", "hi-IN,en-IN").split(",")
        if code.strip()
    ]
    features = None
    if hint:
        features = cloud_speech.RecognitionFeatures(
            custom_prompt_config=cloud_speech.CustomPromptConfig(
                custom_prompt=hint[:4000]
            )
        )
    recognition_config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=language_codes or ["auto"],
        model=os.environ.get("GCP_STT_MODEL", "chirp_3"),
        features=features,
    )
    response = client.recognize(
        request=cloud_speech.RecognizeRequest(
            recognizer=(
                f"projects/{gcp.project_id()}/locations/{location}/recognizers/_"
            ),
            config=recognition_config,
            content=audio_bytes,
        )
    )
    texts: list[str] = []
    detected = "unknown"
    for result in response.results:
        if result.alternatives:
            texts.append(result.alternatives[0].transcript)
        if getattr(result, "language_code", None):
            detected = result.language_code
    return {"text": " ".join(texts).strip(), "lang": _norm_lang(detected)}


def _local_transcribe(audio_bytes: bytes, filename: str, hint: str = "") -> dict:
    """The original local/Groq route, kept intact behind the private switch."""
    if _use_groq():
        return _groq_transcribe(audio_bytes, filename, hint)
    suffix = os.path.splitext(filename or "audio.wav")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        segments, info = _get_model().transcribe(
            path,
            vad_filter=True,
            language=STT_LANGUAGE or None,
            initial_prompt=hint or None,
            temperature=0,
        )
        text = "".join(seg.text for seg in segments).strip()
        return {"text": text, "lang": _norm_lang(getattr(info, "language", None))}
    finally:
        os.remove(path)


def transcribe(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    hint: str = "",
    duration_ms: int = 0,
) -> dict:
    """Speech bytes -> {'text', 'lang'}.

    `hint` is expected vocabulary (party names, ledger words). Both backends use
    it to bias decoding, which markedly improves names and Hinglish phrasing.
    """
    max_bytes = int(os.environ.get("VOICE_MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))
    if not audio_bytes:
        raise ValueError("empty audio recording")
    if len(audio_bytes) > max_bytes:
        raise ValueError("audio recording is too large")
    if duration_ms and duration_ms > 32_000:
        raise ValueError("audio recording must be 30 seconds or shorter")
    if config.gcp_enabled():
        try:
            return _gcp_transcribe(audio_bytes, hint)
        except Exception:
            if not config.GCP_ALLOW_LOCAL_FALLBACK:
                raise
    return _local_transcribe(audio_bytes, filename, hint)


def _gcp_synthesize(text: str, lang: str) -> bytes:
    """Text -> MP3 through a natural Indian Chirp 3 HD voice."""
    from google.api_core.client_options import ClientOptions
    from google.cloud import texttospeech

    location = os.environ.get("GCP_TTS_LOCATION", "global").strip() or "global"
    client_options = None
    if location != "global":
        client_options = ClientOptions(
            api_endpoint=f"{location}-texttospeech.googleapis.com"
        )
    client = texttospeech.TextToSpeechClient(client_options=client_options)
    base_lang = (lang or "hi").split("-")[0].lower()
    language_code = "en-IN" if base_lang == "en" else "hi-IN"
    default_voice = (
        "en-IN-Chirp3-HD-Aoede" if base_lang == "en" else "hi-IN-Chirp3-HD-Aoede"
    )
    voice_name = os.environ.get(
        "GCP_TTS_VOICE_EN" if base_lang == "en" else "GCP_TTS_VOICE_HI",
        default_voice,
    )
    response = client.synthesize_speech(
        request={
            "input": texttospeech.SynthesisInput(text=text),
            "voice": texttospeech.VoiceSelectionParams(
                language_code=language_code,
                name=voice_name,
            ),
            "audio_config": texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=float(os.environ.get("GCP_TTS_SPEAKING_RATE", "1.0")),
            ),
        }
    )
    return bytes(response.audio_content)


async def _edge_synth(text: str, voice: str) -> bytes:
    import edge_tts  # lazy
    buf = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.extend(chunk["data"])
    return bytes(buf)


# Emoji and other pictographs get read out by name ("folded hands", "robot
# face"), which sounds wrong mid-sentence. Strip them from what is spoken; the
# reply shown on screen keeps them.
_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"  # symbols, pictographs, emoticons, extended-A
    "\U0001f000-\U0001f2ff"  # tiles, enclosed characters
    "\U00002300-\U000023ff"  # misc technical: watches, hourglasses, ⏰ ⏳
    "\U000025a0-\U000025ff"  # geometric shapes: ▶ ◼
    "\U00002600-\U000027bf"  # misc symbols and dingbats
    "\U00002b00-\U00002bff"  # arrows and misc symbols
    "\U0001f1e6-\U0001f1ff"  # regional indicators (flags)
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U00002190-\U000021ff"  # arrows
    "\U0000200d"             # zero-width joiner, glues multi-part emoji
    "]+"
)


def speakable(text: str) -> str:
    """The part of a reply worth reading aloud: no emoji, no double spaces."""
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", text)).strip()


def synthesize(text: str, lang: str = "hi") -> bytes:
    """Text -> MP3 audio bytes (empty if text blank). No API key.

    Called from a worker thread (sync route), so asyncio.run is safe here.
    """
    text = speakable(text)
    if not text:
        return b""
    if config.gcp_enabled():
        try:
            return _gcp_synthesize(text, lang)
        except Exception:
            if not config.GCP_ALLOW_LOCAL_FALLBACK:
                raise
    return asyncio.run(_edge_synth(text, _voice_for(lang)))
