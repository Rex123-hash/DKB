"""Tests for the offline Hinglish intent parser."""
from datetime import datetime

import pytest

from app.parser import parse, _extract_due


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


@pytest.mark.parametrize(
    "msg, names",
    [
        ("Ramesh ka khaata banao", ["Ramesh"]),
        ("Ramesh aur Suresh aur Mukesh ka khaata banao", ["Ramesh", "Suresh", "Mukesh"]),
        ("Ramesh, Suresh aur Mukesh ka khata bana do", ["Ramesh", "Suresh", "Mukesh"]),
        ("create account for Ramesh and Suresh", ["Ramesh", "Suresh"]),
        ("naya khaata kholo Mohan ka", ["Mohan"]),
    ],
)
def test_create_intents(msg, names):
    i = parse(msg)
    assert i.action == "create"
    assert i.names == names


def test_create_supplier_type():
    i = parse("Sharma ka supplier khaata banao")
    assert i.action == "create"
    assert i.party_type == "supplier"
    assert i.names == ["Sharma"]


def test_create_with_inline_phone():
    i = parse("Ramesh ka khaata banao 9876543210")
    assert i.action == "create"
    assert i.names == ["Ramesh"]
    assert i.phone == "9876543210"


def test_ledger_add_is_not_mistaken_for_create():
    # has a make-ish verb 'likho' but no account word -> stays an add
    assert parse("Ramesh ko 500 udhaar likho").action == "add"


def test_balance_with_khaata_is_not_create():
    # account word present but no make verb -> balance, not create
    assert parse("Ramesh ka khata kitna baaki hai").action == "balance"


@pytest.mark.parametrize(
    "msg, party, amount",
    [
        ("Rahul ko kal 5000 ke payment ke liye call karna", "Rahul", 5000),
        ("Suresh ko kal yaad dilana 2500", "Suresh", 2500),
        ("Amit ko Monday 10 baje payment follow-up ke liye call karna hai", "Amit", None),
    ],
)
def test_reminder_intents(msg, party, amount):
    i = parse(msg)
    assert i.action == "reminder"
    assert i.party == party
    assert i.amount == amount
    assert i.due_at  # an ISO datetime was produced


def test_reminder_does_not_eat_amount_from_time():
    # "10 baje" must not be read as ₹10; the real amount is 5000
    i = parse("Rahul ko kal 10 baje 5000 ke liye call karna")
    assert i.action == "reminder"
    assert i.amount == 5000


def test_ledger_add_is_not_reminder():
    assert parse("Ramesh ko 500 udhaar likho").action == "add"


@pytest.mark.parametrize(
    "msg, party, phone",
    [
        ("Aman ka phone 9876543210", "Aman", "9876543210"),
        ("Suresh ka number update karo 9123456780", "Suresh", "9123456780"),
        ("Ramesh ka mobile 98765 43210 save karo", "Ramesh", "9876543210"),
    ],
)
def test_set_phone_intents(msg, party, phone):
    i = parse(msg)
    assert i.action == "set_phone"
    assert i.party == party
    assert i.phone == phone


def test_create_with_phone_is_not_set_phone():
    # account+make verb present -> create (handles inline phone itself), not set_phone
    assert parse("Mohan ka khaata banao 9876500000").action == "create"


def test_extract_due_relative_and_time():
    now = datetime(2026, 6, 23, 9, 0)  # a Tuesday
    assert _extract_due("kal", now).startswith("2026-06-24T10:00")
    assert _extract_due("kal 3 pm", now).startswith("2026-06-25T15:00") is False  # kal=24th
    assert _extract_due("kal 3 pm", now).startswith("2026-06-24T15:00")
    assert _extract_due("monday 10 baje", now).startswith("2026-06-29T10:00")  # next Monday
    assert _extract_due("aaj", now).startswith("2026-06-23T10:00")
    # time of day: shaam/raat -> PM, subah stays AM
    assert _extract_due("kal shaam 5 baje", now).startswith("2026-06-24T17:00")
    assert _extract_due("aaj raat 9 baje", now).startswith("2026-06-23T21:00")
    assert _extract_due("kal subah 8 baje", now).startswith("2026-06-24T08:00")
    # Devanagari (voice) date + time
    assert _extract_due("कल शाम 5 बजे", now).startswith("2026-06-24T17:00")
    assert _extract_due("आज 10 बजे", now).startswith("2026-06-23T10:00")


def test_spoken_word_amounts():
    from app.parser import _extract_amount
    assert _extract_amount("रिया से पान्सो रूपे मांगो") == 500      # Devanagari 'paanso'
    assert _extract_amount("Ramesh se paanch sau maango") == 500   # roman 'paanch sau'
    assert _extract_amount("do hazaar udhaar") == 2000
    assert _extract_amount("sau rupaye") == 100
    assert _extract_amount("Ramesh ko 500 udhaar") == 500          # digits still win


def test_extract_due_ignores_garbage_time_no_crash():
    now = datetime(2026, 6, 23, 9, 0)
    # out-of-range times must not raise; fall back to the 10:00 default
    assert _extract_due("kal 50 baje", now).startswith("2026-06-24T10:00")
    assert _extract_due("kal 4:99 pm", now).startswith("2026-06-24T10:00")


def test_reminder_phone_is_not_read_as_amount():
    i = parse("Ramesh ko 9876543210 pe kal call karna")
    assert i.action == "reminder"
    assert i.amount is None  # the phone number is not the rupee amount


def test_spoken_reminder_extracts_word_time_amount_and_phone():
    i = parse("mujhe kal paanch baje paanch sau rupaye ka reminder, phone 8700048065")
    assert i.party == "mujhe"
    assert i.amount == 500
    assert i.phone == "8700048065"
    assert i.due_at.endswith("05:00")
    assert i.reminder_date_provided is True
    assert i.reminder_time_provided is True


def test_reminder_tracks_missing_date_and_time_without_treating_defaults_as_user_input():
    missing_time = parse("Ram ko kal 500 ka reminder lagao")
    assert missing_time.reminder_date_provided is True
    assert missing_time.reminder_time_provided is False

    missing_date = parse("Ram ko 6 PM 500 ka reminder lagao")
    assert missing_date.reminder_date_provided is False
    assert missing_date.reminder_time_provided is True
