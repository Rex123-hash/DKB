"""Voice tests — library-based (faster-whisper + edge-tts), mocked, no model load."""
import base64
import importlib

import pytest
from fastapi.testclient import TestClient

from app import voice


def test_voice_for_lang_mapping():
    assert voice._voice_for("hi") == "hi-IN-SwaraNeural"
    assert voice._voice_for("en") == "en-IN-NeerjaNeural"
    assert voice._voice_for("hi-IN") == "hi-IN-SwaraNeural"
    assert voice._voice_for(None) == voice._DEFAULT_VOICE
    assert voice._voice_for("xx") == voice._DEFAULT_VOICE  # unknown -> default (Hinglish)


def test_synthesize_empty_text_returns_empty():
    assert voice.synthesize("   ") == b""

def test_internal_recognition_hint_cannot_leak_into_chat():
    hint = voice.build_hint(["Ramesh", "Suresh", "Verma Traders"])
    assert voice._clean_transcript(hint, hint) == ""
    assert voice._clean_transcript("Ramesh ko 500 udhaar likho", hint) == (
        "Ramesh ko 500 udhaar likho"
    )




@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import config, db
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "has_voice", lambda: True)
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main.config, "has_voice", lambda: True)
    monkeypatch.setattr(main.voice, "transcribe",
                        lambda audio, fn="a.wav", hint="": {"text": "Ramesh ko 500 udhaar likho", "lang": "hi"})
    monkeypatch.setattr(main.voice, "synthesize", lambda text, lang="hi": b"MP3DATA")
    with TestClient(main.app) as c:
        yield c


def test_voice_chat_endpoint(client):
    r = client.post("/voice/chat", files={"file": ("a.wav", b"xxxx", "audio/wav")})
    assert r.status_code == 200
    data = r.json()
    assert data["transcript"] == "Ramesh ko 500 udhaar likho"
    assert "500" in data["reply"]
    assert data["audio_b64"] == base64.b64encode(b"MP3DATA").decode()


def test_voice_chat_503_when_unavailable(tmp_path, monkeypatch):
    from app import config, db
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "t2.db")
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main.config, "has_voice", lambda: False)
    with TestClient(main.app) as c:
        r = c.post("/voice/chat", files={"file": ("a.wav", b"xxxx", "audio/wav")})
    assert r.status_code == 503
