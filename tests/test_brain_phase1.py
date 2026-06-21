"""End-to-end tests for the Phase 1 offline brain (no Groq key)."""
from app import brain, db


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def test_add_credit_flow():
    conn = _fresh()
    out = brain.respond("Ramesh ko 500 udhaar likho", conn=conn)
    assert "500" in out and "Ramesh" in out
    # persisted
    assert tools_balance(conn, "Ramesh") == 500.0


def test_credit_then_balance_query():
    conn = _fresh()
    brain.respond("Ramesh ko 500 udhaar likho", conn=conn)
    brain.respond("Ramesh ne 200 jama kiye", conn=conn)
    out = brain.respond("Ramesh kitna baaki hai", conn=conn)
    assert "300" in out


def test_balance_unknown_party():
    conn = _fresh()
    out = brain.respond("Mohan kitna baaki hai", conn=conn)
    assert "nahi mila" in out


def test_unknown_message_is_helpful():
    conn = _fresh()
    out = brain.respond("namaste", conn=conn)
    assert "udhaar" in out.lower()


def tools_balance(conn, name):
    from app import tools
    return tools.get_party_balance(conn, name)["balance"]
