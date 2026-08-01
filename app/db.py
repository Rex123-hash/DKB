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
    title     TEXT,
    section   TEXT,
    line_from INTEGER,
    line_to   INTEGER,
    chunk_key TEXT,
    embedding BLOB
);

CREATE TABLE IF NOT EXISTS trace_event (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    question     TEXT,
    payload_json TEXT NOT NULL,
    latency_ms   REAL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bill_draft (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (
        status IN (
            'uploaded', 'extracting', 'needs_information',
            'ready_for_review', 'confirmed', 'posting',
            'finalized', 'failed'
        )
    ),
    source_filename     TEXT NOT NULL,
    source_mime         TEXT NOT NULL,
    source_path         TEXT NOT NULL,
    source_sha256       TEXT NOT NULL,
    extractor_backend   TEXT,
    data_json           TEXT NOT NULL DEFAULT '{}',
    calculation_json    TEXT NOT NULL DEFAULT '{}',
    error               TEXT,
    bill_id             INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bill_draft_session
    ON bill_draft(session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bill_draft_sha
    ON bill_draft(session_id, source_sha256);

CREATE TABLE IF NOT EXISTS bill (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            TEXT NOT NULL UNIQUE REFERENCES bill_draft(id),
    type                TEXT NOT NULL CHECK (type IN ('sale', 'purchase')),
    bill_number         TEXT NOT NULL,
    bill_date           TEXT NOT NULL,
    party_id            INTEGER NOT NULL REFERENCES party(id),
    party_name          TEXT NOT NULL,
    party_phone         TEXT,
    gstin               TEXT,
    gst_mode            TEXT NOT NULL CHECK (gst_mode IN ('gst', 'non_gst')),
    tax_scheme          TEXT CHECK (tax_scheme IN ('cgst_sgst', 'igst')),
    gst_rate            TEXT,
    payment_status      TEXT NOT NULL CHECK (
        payment_status IN ('paid', 'credit', 'partial')
    ),
    subtotal_paise      INTEGER NOT NULL,
    discount_paise      INTEGER NOT NULL DEFAULT 0,
    extra_charge_paise  INTEGER NOT NULL DEFAULT 0,
    taxable_paise       INTEGER NOT NULL,
    cgst_paise          INTEGER NOT NULL DEFAULT 0,
    sgst_paise          INTEGER NOT NULL DEFAULT 0,
    igst_paise          INTEGER NOT NULL DEFAULT 0,
    round_off_paise     INTEGER NOT NULL DEFAULT 0,
    grand_total_paise   INTEGER NOT NULL,
    paid_paise          INTEGER NOT NULL DEFAULT 0,
    note                TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bill_type_date ON bill(type, bill_date DESC);
CREATE INDEX IF NOT EXISTS idx_bill_party ON bill(party_id, bill_date DESC);

CREATE TABLE IF NOT EXISTS bill_item (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id             INTEGER NOT NULL REFERENCES bill(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    quantity            TEXT NOT NULL,
    unit                TEXT,
    unit_price_paise    INTEGER NOT NULL,
    line_total_paise    INTEGER NOT NULL,
    written_total_paise INTEGER,
    hsn                 TEXT,
    gst_rate            TEXT
);

CREATE TABLE IF NOT EXISTS product (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL COLLATE NOCASE UNIQUE,
    quantity    TEXT NOT NULL DEFAULT '0',
    unit        TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_movement (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id     INTEGER NOT NULL REFERENCES bill(id),
    product_id  INTEGER NOT NULL REFERENCES product(id),
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    quantity    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cashbook_entry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id     INTEGER REFERENCES bill(id),
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    amount_paise INTEGER NOT NULL CHECK (amount_paise > 0),
    note        TEXT,
    entry_date  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (bill_id)
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
    # FastAPI may create and clean up a sync dependency on different worker
    # threads. Each request still gets its own connection, but SQLite must allow
    # that connection to cross the dependency/endpoint thread boundary.
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
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

_KB_CHUNK_MIGRATIONS = [
    ("title", "title TEXT"),
    ("section", "section TEXT"),
    ("line_from", "line_from INTEGER"),
    ("line_to", "line_to INTEGER"),
    ("chunk_key", "chunk_key TEXT"),
]


def _ensure_columns(
    conn: sqlite3.Connection, table: str, migrations: list[tuple[str, str]]
) -> None:
    have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, ddl in migrations:
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _ensure_fts(conn: sqlite3.Connection) -> None:
    """Create and synchronize the optional SQLite full-text index."""
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunk_fts USING fts5(
                text,
                title,
                section,
                source,
                tokenize = 'unicode61'
            )
            """
        )
        kb_count = conn.execute("SELECT COUNT(*) FROM kb_chunk").fetchone()[0]
        fts_count = conn.execute("SELECT COUNT(*) FROM kb_chunk_fts").fetchone()[0]
        if kb_count != fts_count:
            conn.execute("DELETE FROM kb_chunk_fts")
            conn.execute(
                """
                INSERT INTO kb_chunk_fts(rowid, text, title, section, source)
                SELECT id, text, COALESCE(title, ''), COALESCE(section, ''),
                       COALESCE(source, '')
                FROM kb_chunk
                """
            )
    except sqlite3.OperationalError:
        # Some minimal SQLite builds omit FTS5. Dense retrieval remains usable.
        pass


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotently upgrade pre-existing operational and RAG tables."""
    _ensure_columns(conn, "reminder", _REMINDER_MIGRATIONS)
    _ensure_columns(conn, "kb_chunk", _KB_CHUNK_MIGRATIONS)
    _ensure_fts(conn)
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not exist, then apply column migrations."""
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()


def has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'kb_chunk_fts'"
    ).fetchone()
    return row is not None


def replace_kb_fts(conn: sqlite3.Connection) -> None:
    """Synchronize FTS rows after a knowledge-base ingest or cache load."""
    if not has_fts(conn):
        return
    conn.execute("DELETE FROM kb_chunk_fts")
    conn.execute(
        """
        INSERT INTO kb_chunk_fts(rowid, text, title, section, source)
        SELECT id, text, COALESCE(title, ''), COALESCE(section, ''),
               COALESCE(source, '')
        FROM kb_chunk
        """
    )
    conn.commit()


def log_trace(
    conn: sqlite3.Connection,
    run_id: str,
    event_type: str,
    payload_json: str,
    question: str | None = None,
    latency_ms: float | None = None,
) -> int:
    """Persist a compact retrieval/agent trace for evaluation and debugging."""
    cur = conn.execute(
        """
        INSERT INTO trace_event (
            run_id, event_type, question, payload_json, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, event_type, question, payload_json, latency_ms, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_traces(conn: sqlite3.Connection, limit: int = 25):
    return conn.execute(
        """
        SELECT id, run_id, event_type, question, payload_json, latency_ms,
               created_at
        FROM trace_event
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(int(limit), 500)),),
    ).fetchall()


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
