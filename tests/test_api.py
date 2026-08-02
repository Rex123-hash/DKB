"""API tests for the structured Ledger endpoints (isolated temp DB)."""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "test.db")
    main = importlib.import_module("app.main")
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_create_party_and_transaction_and_detail(client):
    pid = client.post("/parties", json={"name": "Ramesh", "type": "customer"}).json()["id"]

    r = client.post("/transactions", json={"party_id": pid, "type": "credit", "amount": 500})
    assert r.json()["balance"] == 500.0
    client.post("/transactions", json={"party_id": pid, "type": "debit", "amount": 200})

    detail = client.get(f"/parties/{pid}").json()
    assert detail["balance"] == 300.0
    assert len(detail["transactions"]) == 2


def test_list_parties(client):
    client.post("/parties", json={"name": "Suresh", "type": "supplier"})
    names = [p["name"] for p in client.get("/parties").json()]
    assert "Suresh" in names


def test_transaction_unknown_party_404(client):
    r = client.post("/transactions", json={"party_id": 999, "type": "credit", "amount": 100})
    assert r.status_code == 404


def test_chat_still_works(client):
    r = client.post("/chat", json={"message": "namaste"})
    assert r.status_code == 200 and "reply" in r.json()


def test_reminders_flow(client):
    pid = client.post(
        "/parties",
        json={"name": "Ramesh", "type": "customer", "phone": "9876543210"},
    ).json()["id"]
    rid = client.post("/reminders",
                      json={"party_id": pid, "due_at": "2026-06-20T10:00:00",
                            "message": "udhaar yaad", "amount": 500}).json()["id"]
    pending = client.get("/reminders").json()
    assert len(pending) == 1 and pending[0]["party_name"] == "Ramesh"
    client.post(f"/reminders/{rid}/done")
    assert client.get("/reminders").json() == []


def test_reminder_api_requires_phone_or_explicit_skip(client):
    pid = client.post("/parties", json={"name": "Sita", "type": "customer"}).json()["id"]
    payload = {"party_id": pid, "due_at": "2026-06-20T10:00:00", "amount": 500}

    blocked = client.post("/reminders", json=payload)
    assert blocked.status_code == 409
    assert client.get("/reminders").json() == []

    skipped = client.post("/reminders", json={**payload, "skip_phone": True})
    assert skipped.status_code == 200


def test_typed_chat_is_silent_unless_speaking_is_requested(client, monkeypatch):
    from app import config, main, voice

    monkeypatch.setattr(config, "has_voice", lambda: True)
    monkeypatch.setattr(main.config, "has_voice", lambda: True)
    monkeypatch.setattr(voice, "synthesize", lambda text, lang="hi": b"ID3audio")

    silent = client.post("/chat", json={"message": "namaste"}).json()
    assert silent["audio_b64"] is None

    spoken = client.post("/chat", json={"message": "namaste", "speak": True}).json()
    assert spoken["audio_b64"] and spoken["reply"] == silent["reply"]


def test_speaking_failure_never_costs_the_written_reply(client, monkeypatch):
    from app import config, main, voice

    def _boom(text, lang="hi"):
        raise RuntimeError("tts down")

    monkeypatch.setattr(config, "has_voice", lambda: True)
    monkeypatch.setattr(main.config, "has_voice", lambda: True)
    monkeypatch.setattr(voice, "synthesize", _boom)

    body = client.post("/chat", json={"message": "namaste", "speak": True}).json()
    assert body["audio_b64"] is None and body["reply"]
