"""SQLite data layer for the Dukanbook AI assistant.

Phase 0: schema + minimal helpers. The schema already models the full ledger
(parties, transactions, reminders, kb_chunks) so later phases can build on it
without a migration.

Balance convention for a party = sum(credit) - sum(debit), where:
  - credit  = the shopkeeper gave goods/money on udhaar (party owes shopkeeper)
  - debit   = the party paid back / shopkeeper received
A positive balance means the party still owes the shopkeeper.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# In production (e.g. Render) point DUKANBOOK_DB at a persistent disk mount such
# as /data/dukanbook.db, so the ledger survives restarts and redeploys.
DEFAULT_DB_PATH = Path(
    os.environ.get("DUKANBOOK_DB")
    or Path(__file__).resolve().parent.parent / "dukanbook.db"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS party (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('customer', 'supplier')),
    phone      TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS "transaction" (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id  INTEGER NOT NULL REFERENCES party(id),
    type      TEXT NOT NULL CHECK (type IN ('credit', 'debit')),
    amount    REAL NOT NULL CHECK (amount > 0),
    note      TEXT,
    txn_date  TEXT NOT NULL,
    source    TEXT NOT NULL DEFAULT 'text' CHECK (source IN ('voice', 'text'))
);

CREATE TABLE IF NOT EXISTS reminder (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id   INTEGER NOT NULL REFERENCES party(id),
    due_at     TEXT NOT NULL,
    message    TEXT,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done')),
    amount     REAL,
    channel    TEXT NOT NULL DEFAULT 'call' CHECK (channel IN ('call', 'whatsapp')),
    phone      TEXT,
    created_by TEXT NOT NULL DEFAULT 'owner',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS kb_chunk (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    text      TEXT NOT NULL,
    source    TEXT,
    embedding BLOB
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys on and row access by name.

    Resolves the default path at call time so tests can monkeypatch
    ``db.DEFAULT_DB_PATH``.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH
    parent = Path(db_path).parent
    if str(parent) and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added to `reminder` after its original 5-column shape, so existing
# databases can be upgraded in place. (col_name, ALTER definition).
_REMINDER_MIGRATIONS = [
    ("amount", "amount REAL"),
    ("channel", "channel TEXT NOT NULL DEFAULT 'call'"),
    ("phone", "phone TEXT"),
    ("created_by", "created_by TEXT NOT NULL DEFAULT 'owner'"),
    ("created_at", "created_at TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotently add new reminder columns to a pre-existing database."""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(reminder)")}
    for col, ddl in _REMINDER_MIGRATIONS:
        if col not in have:
            conn.execute(f"ALTER TABLE reminder ADD COLUMN {ddl}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist, then apply column migrations."""
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def add_party(conn: sqlite3.Connection, name: str, type: str, phone: str | None = None) -> int:
    cur = conn.execute(
        'INSERT INTO party (name, type, phone, created_at) VALUES (?, ?, ?, ?)',
        (name, type, phone, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def add_transaction(
    conn: sqlite3.Connection,
    party_id: int,
    type: str,
    amount: float,
    note: str | None = None,
    source: str = "text",
) -> int:
    cur = conn.execute(
        'INSERT INTO "transaction" (party_id, type, amount, note, txn_date, source) '
        "VALUES (?, ?, ?, ?, ?, ?)",
        (party_id, type, amount, note, _now(), source),
    )
    conn.commit()
    return int(cur.lastrowid)


def find_party_by_name(conn: sqlite3.Connection, name: str):
    """Case-insensitive lookup of a party by name. Returns a Row or None."""
    return conn.execute(
        "SELECT * FROM party WHERE lower(name) = lower(?) ORDER BY id LIMIT 1",
        (name.strip(),),
    ).fetchone()


def get_or_create_party(
    conn: sqlite3.Connection, name: str, type: str = "customer"
) -> int:
    """Return the id of an existing party (by name) or create one."""
    row = find_party_by_name(conn, name)
    if row is not None:
        return int(row["id"])
    return add_party(conn, name, type)


def set_party_phone(conn: sqlite3.Connection, party_id: int, phone: str) -> None:
    """Store/overwrite a party's phone number."""
    conn.execute("UPDATE party SET phone = ? WHERE id = ?", (phone, party_id))
    conn.commit()


def list_parties(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM party ORDER BY name").fetchall()


def get_party(conn: sqlite3.Connection, party_id: int):
    return conn.execute("SELECT * FROM party WHERE id = ?", (party_id,)).fetchone()


def get_transactions(conn: sqlite3.Connection, party_id: int):
    return conn.execute(
        'SELECT * FROM "transaction" WHERE party_id = ? ORDER BY id DESC',
        (party_id,),
    ).fetchall()


def add_reminder(
    conn: sqlite3.Connection,
    party_id: int,
    due_at: str,
    message: str | None = None,
    amount: float | None = None,
    channel: str = "call",
    phone: str | None = None,
    created_by: str = "owner",
) -> int:
    if channel not in ("call", "whatsapp"):
        raise ValueError(f"channel must be call|whatsapp, got {channel!r}")
    cur = conn.execute(
        "INSERT INTO reminder "
        "(party_id, due_at, message, status, amount, channel, phone, created_by, created_at) "
        "VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
        (party_id, due_at, message, amount, channel, phone, created_by, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_reminders(conn: sqlite3.Connection, status: str | None = None):
    sql = (
        "SELECT r.id, r.party_id, r.due_at, r.message, r.status, r.amount, r.channel, "
        "r.phone, r.created_by, r.created_at, p.name AS party_name "
        "FROM reminder r JOIN party p ON p.id = r.party_id "
    )
    params: tuple = ()
    if status:
        sql += "WHERE r.status = ? "
        params = (status,)
    sql += "ORDER BY r.due_at"
    return conn.execute(sql, params).fetchall()


def mark_reminder_done(conn: sqlite3.Connection, reminder_id: int) -> None:
    conn.execute("UPDATE reminder SET status = 'done' WHERE id = ?", (reminder_id,))
    conn.commit()


def get_balance(conn: sqlite3.Connection, party_id: int) -> float:
    """Return party balance = sum(credit) - sum(debit). Positive = party owes."""
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'credit' THEN amount ELSE 0 END), 0)
          - COALESCE(SUM(CASE WHEN type = 'debit'  THEN amount ELSE 0 END), 0) AS balance
        FROM "transaction" WHERE party_id = ?
        """,
        (party_id,),
    ).fetchone()
    return float(row["balance"])
