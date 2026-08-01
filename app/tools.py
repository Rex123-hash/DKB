"""Ledger 'tools' the brain can call.

Each tool is a plain function taking a DB connection plus arguments and
returning a small dict result. These are what the Groq LLM calls via
function-calling, and what the offline parser path calls directly — so the
ledger behaviour is identical whether or not an LLM key is present.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from app import db, rag


def normalize_phone(raw: str) -> str | None:
    """Return a 10-digit Indian mobile number, or None if not valid.

    Accepts common forms: spaces/dashes, a leading +91 / 91 / 0. The final
    number must be 10 digits starting 6-9 (Indian mobile series).
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return None


def create_accounts(conn, names: list[str], party_type: str = "customer") -> dict:
    """Create accounts for each name, skipping names that already exist.

    Returns {'created': [{'id','name'}...], 'existing': [name...]}.
    Names are matched case-insensitively so the same person is never duplicated.
    """
    created: list[dict] = []
    existing: list[str] = []
    for raw in names:
        name = (raw or "").strip()
        if not name:
            continue
        if db.find_party_by_name(conn, name) is not None:
            existing.append(name)
            continue
        pid = db.add_party(conn, name, party_type)
        created.append({"id": pid, "name": name})
    return {"created": created, "existing": existing}


def set_phone(conn, party_name: str, phone: str) -> dict:
    """Validate and store a phone for an existing party.

    Returns {'party','phone'} on success, or {'error': ...} if the number is
    invalid or the party does not exist.
    """
    row = db.find_party_by_name(conn, party_name)
    if row is None:
        return {"error": "not_found", "party": party_name}
    normalized = normalize_phone(phone)
    if normalized is None:
        return {"error": "invalid_phone", "party": party_name}
    db.set_party_phone(conn, int(row["id"]), normalized)
    return {"party": row["name"], "phone": normalized}


def search_knowledge(conn, query: str, k: int = 5) -> list[dict]:
    """Retrieve accounting/GST/tax + business-advice passages for a question."""
    return rag.search(conn, query, k=k)


def add_ledger_entry(
    conn,
    party_name: str,
    txn_type: str,
    amount: float,
    party_type: str = "customer",
    note: str | None = None,
    source: str = "text",
) -> dict:
    """Record a credit/debit against a party (creating the party if new).

    credit = party now owes the shopkeeper more (gave goods / lent).
    debit  = party paid back / shopkeeper received money.
    """
    if txn_type not in ("credit", "debit"):
        raise ValueError(f"txn_type must be credit|debit, got {txn_type!r}")
    if amount <= 0:
        raise ValueError("amount must be positive")
    pid = db.get_or_create_party(conn, party_name, party_type)
    db.add_transaction(conn, pid, txn_type, float(amount), note=note, source=source)
    return {
        "party": party_name,
        "type": txn_type,
        "amount": float(amount),
        "balance": db.get_balance(conn, pid),
    }


def get_party_balance(conn, party_name: str) -> dict | None:
    """Return {'party', 'balance'} or None if the party does not exist."""
    row = db.find_party_by_name(conn, party_name)
    if row is None:
        return None
    return {"party": row["name"], "balance": db.get_balance(conn, int(row["id"]))}


def whatsapp_link(phone: str | None, text: str | None = None) -> str | None:
    """Build a no-key wa.me click-to-send link, or None if the phone is unusable."""
    digits = normalize_phone(phone or "")
    if digits is None:
        return None
    base = f"https://wa.me/91{digits}"
    return f"{base}?text={quote(text)}" if text else base


def call_link(phone: str | None) -> str | None:
    """Build a no-key click-to-dial tel: link, or None if the phone is unusable."""
    digits = normalize_phone(phone or "")
    return f"tel:+91{digits}" if digits else None


def _reminder_text(party_name: str, amount: float | None, message: str | None) -> str:
    """Default Hinglish WhatsApp follow-up text the shopkeeper can send."""
    parts = [f"Namaste {party_name} ji,"]
    if amount:
        parts.append(f"aapka ₹{amount:.0f} ka payment pending hai.")
    elif message:
        parts.append(message)
    else:
        parts.append("ek chhoti si yaad-dilani thi.")
    parts.append("Kripya jaldi clear karein. Dhanyavaad.")
    return " ".join(parts)


def schedule_reminder(
    conn,
    party_name: str,
    due_at: str,
    message: str | None = None,
    amount: float | None = None,
    channel: str = "call",
) -> dict:
    """Create a payment/call reminder (call request) for a party at an ISO due_at.

    Snapshots the party's phone onto the request and builds a no-key WhatsApp link.
    """
    pid = db.get_or_create_party(conn, party_name)
    row = db.get_party(conn, pid)
    phone = row["phone"] if row else None
    rid = db.add_reminder(conn, pid, due_at, message, amount=amount,
                          channel=channel, phone=phone)
    return {
        "id": rid,
        "party": party_name,
        "due_at": due_at,
        "message": message,
        "amount": amount,
        "channel": channel,
        "phone": phone,
        "whatsapp_link": whatsapp_link(phone, _reminder_text(party_name, amount, message)),
        "call_link": call_link(phone),
    }


def list_reminders(conn, status: str = "pending") -> list[dict]:
    return [dict(r) for r in db.list_reminders(conn, status)]


def list_all_parties(conn) -> list[dict]:
    out = []
    for r in db.list_parties(conn):
        out.append(
            {
                "id": int(r["id"]),
                "name": r["name"],
                "type": r["type"],
                "phone": r["phone"],
                "balance": db.get_balance(conn, int(r["id"])),
            }
        )
    return out
