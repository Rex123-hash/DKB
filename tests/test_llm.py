"""Test the Groq tool-calling loop with a faked transport (no real API key)."""
from app import db, llm, tools


def test_call_falls_back_to_local_qwen3_when_primary_fails(monkeypatch):
    """A cloud primary that errors should fall through to the local qwen3:8b model."""
    monkeypatch.setenv("OLLAMA_MODEL", "minimax-m3:cloud")
    monkeypatch.delenv("OLLAMA_FALLBACK_MODEL", raising=False)

    tried: list[str] = []

    class FakeResp:
        def __init__(self, ok, data):
            self._ok, self._data = ok, data

        def raise_for_status(self):
            if not self._ok:
                import requests
                raise requests.HTTPError("primary down")

        def json(self):
            return self._data

    def fake_post(url, headers=None, json=None, timeout=None):
        tried.append(json["model"])
        if json["model"] == "minimax-m3:cloud":
            return FakeResp(False, {})                       # primary fails
        return FakeResp(True, {"choices": [{"message": {"content": "ok"}}]})  # qwen3 works

    monkeypatch.setattr(llm.requests, "post", fake_post)
    data = llm._call("key", [{"role": "user", "content": "hi"}])
    assert data["choices"][0]["message"]["content"] == "ok"
    assert tried == ["minimax-m3:cloud", "qwen3:8b"]         # primary, then fallback


def test_llm_executes_tool_and_returns_final(monkeypatch):
    conn = db.get_connection(":memory:")
    db.init_db(conn)

    calls = {"n": 0}

    def fake_call(api_key, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            # model asks to call the ledger tool
            return {
                "choices": [{"message": {
                    "role": "assistant", "content": None,
                    "tool_calls": [{
                        "id": "call_1", "type": "function",
                        "function": {
                            "name": "add_ledger_entry",
                            "arguments": '{"party_name":"Ramesh","txn_type":"credit","amount":500}',
                        },
                    }],
                }}]
            }
        # second turn: model produces the final reply
        return {"choices": [{"message": {
            "role": "assistant", "content": "Ramesh ko ₹500 udhaar likh diya."}}]}

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    out = llm.run("Ramesh ko 500 udhaar likho", conn)
    assert "500" in out
    # the tool really ran against the DB
    assert tools.get_party_balance(conn, "Ramesh")["balance"] == 500.0
    assert calls["n"] == 2


def test_llm_create_account_tool(monkeypatch):
    conn = db.get_connection(":memory:")
    db.init_db(conn)

    calls = {"n": 0}

    def fake_call(api_key, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {
                        "name": "create_account",
                        "arguments": '{"party_name":"Ramesh","phone":"9876543210"}',
                    },
                }],
            }}]}
        return {"choices": [{"message": {
            "role": "assistant", "content": "Ramesh ka khaata ban gaya."}}]}

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    out = llm.run("Ramesh ka khaata banao", conn)
    assert "Ramesh" in out
    assert db.find_party_by_name(conn, "Ramesh")["phone"] == "9876543210"


def test_llm_schedule_reminder_with_amount(monkeypatch):
    conn = db.get_connection(":memory:")
    db.init_db(conn)

    calls = {"n": 0}

    def fake_call(api_key, messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "r1", "type": "function",
                    "function": {
                        "name": "schedule_reminder",
                        "arguments": '{"party_name":"Rahul","due_at":"2026-06-24T10:00",'
                                     '"amount":5000,"description":"payment follow-up","channel":"whatsapp"}',
                    },
                }],
            }}]}
        return {"choices": [{"message": {
            "role": "assistant", "content": "Rahul ko kal reminder laga diya."}}]}

    monkeypatch.setattr(llm, "_call", fake_call)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    out = llm.run("Rahul ko kal 5000 ke payment ke liye call karna", conn)
    assert "Rahul" in out
    row = db.list_reminders(conn)[0]
    assert row["amount"] == 5000
    assert row["channel"] == "whatsapp"
    assert row["message"] == "payment follow-up"
