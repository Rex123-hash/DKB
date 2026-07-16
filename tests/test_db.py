"""Tests for the SQLite data layer (run against an in-memory DB)."""
import pytest

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


def test_set_party_phone_persists():
    conn = _fresh()
    pid = db.add_party(conn, "Ramesh", "customer")
    db.set_party_phone(conn, pid, "9876543210")
    assert db.get_party(conn, pid)["phone"] == "9876543210"


def test_add_reminder_stores_all_fields():
    conn = _fresh()
    pid = db.add_party(conn, "Rahul", "customer")
    rid = db.add_reminder(conn, pid, "2026-06-24T10:00", "payment follow-up",
                          amount=5000, channel="whatsapp", phone="9876543210")
    r = db.list_reminders(conn)[0]
    assert r["id"] == rid
    assert r["amount"] == 5000
    assert r["channel"] == "whatsapp"
    assert r["phone"] == "9876543210"
    assert r["created_by"] == "owner"
    assert r["created_at"]  # timestamp stamped


def test_add_reminder_rejects_bad_channel():
    conn = _fresh()
    pid = db.add_party(conn, "Rahul", "customer")
    with pytest.raises(ValueError):
        db.add_reminder(conn, pid, "2026-06-24T10:00", channel="carrier-pigeon")


def test_migration_upgrades_old_reminder_table():
    # Simulate a pre-feature DB: reminder table with only the original 5 columns.
    conn = db.get_connection(":memory:")
    conn.executescript(
        """
        CREATE TABLE party (id INTEGER PRIMARY KEY, name TEXT, type TEXT, phone TEXT, created_at TEXT);
        CREATE TABLE reminder (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            party_id INTEGER NOT NULL,
            due_at TEXT NOT NULL,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    conn.commit()
    db.init_db(conn)  # should ALTER in the new columns without error
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(reminder)")}
    assert {"amount", "channel", "phone", "created_by", "created_at"} <= cols
