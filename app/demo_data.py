"""Demo seed / reset helpers — populate a believable shop for presentations.

Keeps the RAG knowledge base (kb_chunk) intact; only ledger data is touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import db


def reset(conn) -> None:
    """Clear all ledger data (parties, transactions, reminders). KB untouched."""
    conn.execute('DELETE FROM "transaction"')
    conn.execute("DELETE FROM reminder")
    conn.execute("DELETE FROM party")
    conn.commit()


def seed(conn) -> dict:
    """Reset, then load a small realistic shop: 2 customers + 1 supplier + a reminder."""
    reset(conn)

    ramesh = db.get_or_create_party(conn, "Ramesh", "customer")
    db.add_transaction(conn, ramesh, "credit", 500, "kirana saman")
    db.add_transaction(conn, ramesh, "debit", 200, "part payment")

    suresh = db.get_or_create_party(conn, "Suresh", "customer")
    db.add_transaction(conn, suresh, "credit", 1200, "mahine ka udhaar")

    verma = db.get_or_create_party(conn, "Verma Traders", "supplier")
    db.add_transaction(conn, verma, "debit", 3000, "stock ka payment baaki")

    due = (datetime.now() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    db.add_reminder(conn, ramesh, due.isoformat(timespec="minutes"), "Ramesh se baaki payment lena")

    return {"parties": 3, "reminders": 1}
