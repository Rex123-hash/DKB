"""Tests for the brain's basic conversational behaviour."""
from app import brain, db


def test_empty_message_gives_greeting():
    assert "Namaste" in brain.respond("   ")


def test_ledger_message_is_processed_not_echoed():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    out = brain.respond("Ramesh ko 500 udhaar likho", conn=conn)
    # it acts on the message rather than echoing it verbatim
    assert "✅" in out
    assert "500" in out
