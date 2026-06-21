"""Tests for the offline Hinglish intent parser."""
import pytest

from app.parser import parse


@pytest.mark.parametrize(
    "msg, action, party, txn_type, amount",
    [
        ("Ramesh ko 500 udhaar likho", "add", "Ramesh", "credit", 500),
        ("Suresh ne 200 jama kiye", "add", "Suresh", "debit", 200),
        ("Suresh ne 300 wapas kar diye", "add", "Suresh", "debit", 300),
        ("add 500 to Ramesh", "add", "Ramesh", "credit", 500),
        ("Ramesh ka 250 ka udhaar", "add", "Ramesh", "credit", 250),
    ],
)
def test_add_intents(msg, action, party, txn_type, amount):
    i = parse(msg)
    assert i.action == action
    assert i.party == party
    assert i.txn_type == txn_type
    assert i.amount == amount


@pytest.mark.parametrize(
    "msg, party",
    [
        ("Ramesh kitna baaki hai", "Ramesh"),
        ("Ramesh ka balance batao", "Ramesh"),
        ("Suresh ka hisaab", "Suresh"),
    ],
)
def test_balance_intents(msg, party):
    i = parse(msg)
    assert i.action == "balance"
    assert i.party == party


def test_list_intent():
    assert parse("saare customers dikhao").action == "list"
    assert parse("list all parties").action == "list"


def test_unknown_intent():
    assert parse("namaste").action == "unknown"
    assert parse("").action == "unknown"
