"""Tests for the ledger tools layer."""
import pytest

from app import db, tools


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def test_add_credit_creates_party_and_balance():
    conn = _fresh()
    res = tools.add_ledger_entry(conn, "Ramesh", "credit", 500)
    assert res["balance"] == 500.0
    # party was auto-created
    assert db.find_party_by_name(conn, "ramesh") is not None


def test_credit_then_debit_balance():
    conn = _fresh()
    tools.add_ledger_entry(conn, "Ramesh", "credit", 500)
    res = tools.add_ledger_entry(conn, "Ramesh", "debit", 200)
    assert res["balance"] == 300.0


def test_get_balance_unknown_party_is_none():
    conn = _fresh()
    assert tools.get_party_balance(conn, "Nobody") is None


def test_invalid_type_rejected():
    conn = _fresh()
    with pytest.raises(ValueError):
        tools.add_ledger_entry(conn, "Ramesh", "sideways", 100)


def test_list_parties_with_balances():
    conn = _fresh()
    tools.add_ledger_entry(conn, "Ramesh", "credit", 500)
    tools.add_ledger_entry(conn, "Suresh", "credit", 100)
    names = {p["name"]: p["balance"] for p in tools.list_all_parties(conn)}
    assert names == {"Ramesh": 500.0, "Suresh": 100.0}


def test_list_parties_includes_phone():
    conn = _fresh()
    tools.create_accounts(conn, ["Ramesh"])
    tools.set_phone(conn, "Ramesh", "9876543210")
    row = next(p for p in tools.list_all_parties(conn) if p["name"] == "Ramesh")
    assert row["phone"] == "9876543210"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("9876543210", "9876543210"),
        ("98765 43210", "9876543210"),
        ("+91 98765-43210", "9876543210"),
        ("098765 43210", "9876543210"),
        ("12345", None),          # too short
        ("1234567890", None),     # starts with 1, not a mobile
        ("98765432101234", None), # too long
    ],
)
def test_normalize_phone(raw, expected):
    assert tools.normalize_phone(raw) == expected


def test_create_accounts_creates_new_and_reports_existing():
    conn = _fresh()
    tools.add_ledger_entry(conn, "Ramesh", "credit", 500)  # Ramesh already exists
    res = tools.create_accounts(conn, ["Ramesh", "Suresh", "Mukesh"])
    assert {c["name"] for c in res["created"]} == {"Suresh", "Mukesh"}
    assert res["existing"] == ["Ramesh"]
    # no duplicate Ramesh
    names = [p["name"] for p in tools.list_all_parties(conn)]
    assert names.count("Ramesh") == 1


def test_create_accounts_supplier_type():
    conn = _fresh()
    tools.create_accounts(conn, ["Wholesaler"], party_type="supplier")
    assert db.find_party_by_name(conn, "Wholesaler")["type"] == "supplier"


def test_set_phone_valid_and_invalid():
    conn = _fresh()
    tools.create_accounts(conn, ["Ramesh"])
    ok = tools.set_phone(conn, "Ramesh", "98765-43210")
    assert ok == {"party": "Ramesh", "phone": "9876543210"}
    bad = tools.set_phone(conn, "Ramesh", "123")
    assert bad["error"] == "invalid_phone"
    missing = tools.set_phone(conn, "Nobody", "9876543210")
    assert missing["error"] == "not_found"


def test_whatsapp_link_builds_and_rejects():
    link = tools.whatsapp_link("9876543210", "Namaste ji")
    assert link.startswith("https://wa.me/919876543210?text=")
    assert "Namaste" in link  # text url-encoded into the link
    assert tools.whatsapp_link("123", "hi") is None  # bad phone -> no link
    assert tools.whatsapp_link(None) is None


def test_schedule_reminder_snapshots_phone_and_link():
    conn = _fresh()
    tools.create_accounts(conn, ["Rahul"])
    tools.set_phone(conn, "Rahul", "9876543210")
    res = tools.schedule_reminder(conn, "Rahul", "2026-06-24T10:00",
                                  message="payment", amount=5000, channel="whatsapp")
    assert res["amount"] == 5000
    assert res["channel"] == "whatsapp"
    assert res["phone"] == "9876543210"
    assert res["whatsapp_link"].startswith("https://wa.me/919876543210")
    # persisted with the phone snapshot
    row = db.list_reminders(conn)[0]
    assert row["phone"] == "9876543210" and row["amount"] == 5000


def test_schedule_reminder_without_phone_has_no_link():
    conn = _fresh()
    res = tools.schedule_reminder(conn, "Ghosh", "2026-06-24T10:00", amount=200)
    assert res["phone"] is None
    assert res["whatsapp_link"] is None
    assert res["call_link"] is None


def test_call_link_builds_and_rejects():
    assert tools.call_link("98765-43210") == "tel:+919876543210"
    assert tools.call_link("123") is None
    assert tools.call_link(None) is None


def test_schedule_reminder_includes_call_link():
    conn = _fresh()
    tools.create_accounts(conn, ["Rahul"])
    tools.set_phone(conn, "Rahul", "9876543210")
    res = tools.schedule_reminder(conn, "Rahul", "2026-06-24T10:00", channel="call")
    assert res["call_link"] == "tel:+919876543210"
