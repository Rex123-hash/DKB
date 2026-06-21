"""Tests for demo seed / reset."""
from app import db, demo_data, tools


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def test_seed_populates_and_reset_clears():
    conn = _fresh()
    info = demo_data.seed(conn)
    assert info["parties"] == 3
    parties = {p["name"]: p["balance"] for p in tools.list_all_parties(conn)}
    assert parties["Ramesh"] == 300.0      # 500 credit - 200 debit
    assert parties["Suresh"] == 1200.0
    assert parties["Verma Traders"] == -3000.0  # supplier we owe
    assert len(tools.list_reminders(conn, "pending")) == 1

    demo_data.reset(conn)
    assert tools.list_all_parties(conn) == []
    assert tools.list_reminders(conn, "pending") == []


def test_seed_is_idempotent():
    conn = _fresh()
    demo_data.seed(conn)
    demo_data.seed(conn)  # should reset first, not duplicate
    assert len(tools.list_all_parties(conn)) == 3
