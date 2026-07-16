"""Shared test setup.

`.env` (loaded by app.config) sets GROQ_API_KEY, which would make the brain
hit the real Groq API during unit tests. Disable it by default so tests are
deterministic and offline; tests that need the LLM re-enable it with a mocked
transport (see test_llm.py).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_llm_by_default(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    # Use the small, already-cached embed model in tests (avoid the 2.24GB e5 download).
    monkeypatch.setenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
