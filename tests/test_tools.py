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
