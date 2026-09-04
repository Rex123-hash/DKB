"""Tests for demo seed / reset."""
from app import db, demo_data, tools


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def test_seed_populates_and_reset_clears():
    conn = _fresh()
    info = demo_data.seed(conn)
    assert info["parties"] == 3
    parties = {p["name"]: p["balance"] for p in tools.list_all_parties(conn)}
    # 500 credit - 200 debit, plus the 1455 credit sale the seed posts.
    assert parties["Ramesh"] == 1755.0
    assert parties["Suresh"] == 1200.0
    # The seeded purchase is paid, so it adds cash movement but no supplier due.
    assert parties["Verma Traders"] == -3000.0  # supplier we owe
    assert len(tools.list_reminders(conn, "pending")) == 1

    demo_data.reset(conn)
    assert tools.list_all_parties(conn) == []
    assert tools.list_reminders(conn, "pending") == []


def test_seed_is_idempotent():
    conn = _fresh()
    demo_data.seed(conn)
    demo_data.seed(conn)  # should reset first, not duplicate
    assert len(tools.list_all_parties(conn)) == 3


def test_seed_fills_every_screen_not_just_the_ledger():
    """A fresh deployment must not show three empty tabs."""
    from app import db, demo_data

    conn = db.get_connection(":memory:")
    db.init_db(conn)
    demo_data.seed(conn)

    assert conn.execute("SELECT COUNT(*) FROM party").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(*) FROM product").fetchone()[0] > 0, "Stock tab empty"
    assert conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0] > 0, "Bills tab empty"
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] > 0, "Cashbook empty"


def test_seeded_stock_is_never_negative():
    """Negative stock reads as a broken app to anyone looking at the demo."""
    from decimal import Decimal
    from app import db, demo_data

    conn = db.get_connection(":memory:")
    db.init_db(conn)
    demo_data.seed(conn)

    quantities = [Decimal(str(r["quantity"])) for r in conn.execute("SELECT quantity FROM product")]
    assert quantities and all(q >= 0 for q in quantities)
