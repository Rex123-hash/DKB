"""Test the Groq tool-calling loop with a faked transport (no real API key)."""
from app import db, llm, tools


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
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    out = llm.run("Ramesh ko 500 udhaar likho", conn)
    assert "500" in out
    # the tool really ran against the DB
    assert tools.get_party_balance(conn, "Ramesh")["balance"] == 500.0
    assert calls["n"] == 2
