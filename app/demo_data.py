"""Demo seed / reset helpers — populate a believable shop for presentations.

Keeps the RAG knowledge base (kb_chunk) intact; only ledger data is touched.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app import db


def reset(conn) -> None:
    """Clear shop/demo data, including billing side effects. KB untouched."""
    conn.execute("DELETE FROM cashbook_entry")
    conn.execute("DELETE FROM stock_movement")
    conn.execute("DELETE FROM bill_item")
    conn.execute("DELETE FROM bill")
    conn.execute("DELETE FROM bill_draft")
    conn.execute("DELETE FROM product")
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

    bills = _seed_bills(conn)

    return {"parties": 3, "reminders": 1, "bills": bills["bills"], "products": bills["products"]}


def _seed_bills(conn) -> dict:
    """Post one purchase and one sale through the real billing path.

    Seeding rows directly would leave stock, cashbook and ledger disagreeing.
    Posting real bills means the Stock, Bills and Cashbook screens show
    consistent figures on a fresh deployment instead of three empty tabs.
    """
    from app.billing import repository as billing_repository
    from app.billing.models import BillDraftData

    today = date.today()
    stocked = (today - timedelta(days=3)).isoformat()
    sold = (today - timedelta(days=1)).isoformat()

    drafts = [
        {
            "document_kind": "bill", "bill_type": "purchase",
            "bill_number": "PUR-1042", "bill_date": stocked,
            "party": {"name": "Verma Traders", "phone": "9876500011"},
            "gst_mode": "non_gst", "payment_status": "paid",
            "items": [
                {"name": "Basmati Rice 5kg", "quantity": "20", "unit": "bag",
                 "unit_price_paise": 42000},
                {"name": "Sunflower Oil 1L", "quantity": "30", "unit": "pcs",
                 "unit_price_paise": 14500},
                {"name": "Toor Dal", "quantity": "25", "unit": "kg",
                 "unit_price_paise": 11800},
            ],
        },
        {
            "document_kind": "bill", "bill_type": "sale",
            "bill_number": "INV-2051", "bill_date": sold,
            "party": {"name": "Ramesh", "phone": "9876543210"},
            "gst_mode": "non_gst", "payment_status": "credit",
            "items": [
                {"name": "Basmati Rice 5kg", "quantity": "2", "unit": "bag",
                 "unit_price_paise": 48000},
                {"name": "Sunflower Oil 1L", "quantity": "3", "unit": "pcs",
                 "unit_price_paise": 16500},
            ],
        },
    ]

    posted = 0
    for index, payload in enumerate(drafts):
        data = BillDraftData.model_validate(payload)
        draft_id = billing_repository.create_draft(
            conn,
            session_id="demo",
            source_filename=f"demo-{index}.jpg",
            source_mime="image/jpeg",
            source_path=f"demo-{index}.jpg",
            source_sha256=f"demo-seed-{index}",
        )
        billing_repository.save_draft_data(conn, draft_id, data, backend="fake")
        billing_repository.finalize_draft(conn, draft_id)
        posted += 1

    products = conn.execute("SELECT COUNT(*) FROM product").fetchone()[0]
    return {"bills": posted, "products": products}
