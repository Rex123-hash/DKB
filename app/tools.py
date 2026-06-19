"""Ledger 'tools' the brain can call.

Each tool is a plain function taking a DB connection plus arguments and
returning a small dict result. These are what the Groq LLM calls via
function-calling, and what the offline parser path calls directly — so the
ledger behaviour is identical whether or not an LLM key is present.
"""
from __future__ import annotations

from app import db, rag


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


def schedule_reminder(conn, party_name: str, due_at: str, message: str | None = None) -> dict:
    """Create a payment/call reminder for a party at an ISO 8601 due_at."""
    pid = db.get_or_create_party(conn, party_name)
    rid = db.add_reminder(conn, pid, due_at, message)
    return {"id": rid, "party": party_name, "due_at": due_at, "message": message}


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
                "balance": db.get_balance(conn, int(r["id"])),
            }
        )
    return out
