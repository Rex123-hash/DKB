"""Tests for the SQLite data layer (run against an in-memory DB)."""
from app import db


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def test_init_creates_tables():
    conn = _fresh()
    names = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"party", "transaction", "reminder", "kb_chunk"} <= names


def test_balance_credit_minus_debit():
    conn = _fresh()
    pid = db.add_party(conn, "Ramesh", "customer")
    db.add_transaction(conn, pid, "credit", 500)   # gave 500 udhaar
    db.add_transaction(conn, pid, "debit", 200)    # got 200 back
    assert db.get_balance(conn, pid) == 300.0


def test_balance_zero_for_new_party():
    conn = _fresh()
    pid = db.add_party(conn, "Suresh", "supplier")
    assert db.get_balance(conn, pid) == 0.0
