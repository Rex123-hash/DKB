from __future__ import annotations

import pytest

from app import config, llm, voice
from app.billing.extractors import FakeBillExtractor, VertexBillExtractor, get_extractor
from app.billing.models import BillDraftData


def test_private_gcp_switch_ignores_environment(monkeypatch):
    monkeypatch.setattr(config, "GCP_ENABLED", False)
    monkeypatch.setenv("GCP_ENABLED", "true")
    assert config.gcp_enabled() is False
    monkeypatch.setattr(config, "GCP_ENABLED", True)
    assert config.gcp_enabled() is True


def test_disabled_switch_keeps_original_local_provider(monkeypatch):
    monkeypatch.setattr(config, "GCP_ENABLED", False)
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    providers = llm._providers()

    assert providers[0][1] == "local-test-model"
    assert "localhost:11434" in providers[0][0]


def test_enabled_switch_puts_vertex_first(monkeypatch):
    from app import gcp

    expected = (
        "https://vertex.example/chat/completions",
        "google/gemini-test",
        {"Authorization": "Bearer test"},
        12,
    )
    monkeypatch.setattr(config, "GCP_ENABLED", True)
    monkeypatch.setattr(gcp, "vertex_openai_provider", lambda: expected)
    monkeypatch.setenv("OLLAMA_MODEL", "local-test-model")

    providers = llm._providers()

    assert providers[0] == expected
    assert providers[1][1] == "local-test-model"


def test_bill_extractor_follows_private_switch(monkeypatch):
    monkeypatch.setattr(config, "GCP_ENABLED", False)
    monkeypatch.setenv("BILL_AI_BACKEND", "fake")
    assert isinstance(get_extractor(), FakeBillExtractor)

    monkeypatch.setattr(config, "GCP_ENABLED", True)
    assert isinstance(get_extractor(), VertexBillExtractor)
    # Explicit internal selection remains available for deterministic tests.
    assert isinstance(get_extractor("fake"), FakeBillExtractor)


def test_vertex_bill_extractor_uses_ocr_as_context(monkeypatch):
    from app import gcp

    payload = BillDraftData(document_kind="bill").model_dump(mode="json")
    payload["party"]["name"] = "Test Store"
    captured = {}
    monkeypatch.setattr(gcp, "document_ai_ocr", lambda *_: "Invoice No 42")

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return payload

    monkeypatch.setattr(gcp, "vertex_generate_content", fake_generate)

    result = VertexBillExtractor().extract(b"image", "image/jpeg", "bill.jpg")

    assert result.party.name == "Test Store"
    assert "Invoice No 42" in captured["parts"][0]["text"]
    assert captured["parts"][1]["inlineData"]["mimeType"] == "image/jpeg"


def test_gcp_voice_primary_and_local_fallback(monkeypatch):
    monkeypatch.setattr(config, "GCP_ENABLED", True)
    monkeypatch.setattr(config, "GCP_ALLOW_LOCAL_FALLBACK", True)
    monkeypatch.setattr(
        voice,
        "_gcp_transcribe",
        lambda *_: {"text": "paanch sau", "lang": "hi"},
    )
    assert voice.transcribe(b"audio", "audio.webm")["text"] == "paanch sau"

    def fail_cloud(*_):
        raise RuntimeError("cloud unavailable")

    monkeypatch.setattr(voice, "_gcp_transcribe", fail_cloud)
    monkeypatch.setattr(
        voice,
        "_local_transcribe",
        lambda *_: {"text": "local answer", "lang": "en"},
    )
    assert voice.transcribe(b"audio", "audio.webm")["text"] == "local answer"


def test_voice_limits_are_checked_before_provider(monkeypatch):
    monkeypatch.setattr(config, "GCP_ENABLED", True)
    monkeypatch.setattr(voice, "_gcp_transcribe", lambda *_: pytest.fail("not called"))

    with pytest.raises(ValueError, match="30 seconds"):
        voice.transcribe(b"audio", duration_ms=33_000)


def test_gcp_tts_can_fall_back_to_existing_voice(monkeypatch):
    monkeypatch.setattr(config, "GCP_ENABLED", True)
    monkeypatch.setattr(config, "GCP_ALLOW_LOCAL_FALLBACK", True)
    monkeypatch.setattr(voice, "_gcp_synthesize", lambda *_: b"gcp-mp3")
    assert voice.synthesize("Namaste", "hi") == b"gcp-mp3"

    monkeypatch.setattr(
        voice,
        "_gcp_synthesize",
        lambda *_: (_ for _ in ()).throw(RuntimeError("cloud unavailable")),
    )

    async def fake_edge(*_):
        return b"edge-mp3"

    monkeypatch.setattr(voice, "_edge_synth", fake_edge)
    assert voice.synthesize("Namaste", "hi") == b"edge-mp3"
