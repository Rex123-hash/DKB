"""SQLite persistence and atomic bill finalization."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from app import tools
from app.billing.calculator import validate_bill
from app.billing.models import BillCalculation, BillDraftData


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_draft(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    source_filename: str,
    source_mime: str,
    source_path: str,
    source_sha256: str,
    draft_id: str | None = None,
) -> str:
    draft_id = draft_id or str(uuid.uuid4())
    now = _now()
    conn.execute(
        """
        INSERT INTO bill_draft (
            id, session_id, status, source_filename, source_mime, source_path,
            source_sha256, data_json, calculation_json, created_at, updated_at
        ) VALUES (?, ?, 'uploaded', ?, ?, ?, ?, '{}', '{}', ?, ?)
        """,
        (
            draft_id,
            session_id,
            source_filename,
            source_mime,
            source_path,
            source_sha256,
            now,
            now,
        ),
    )
    conn.commit()
    return draft_id


def find_duplicate_draft(
    conn: sqlite3.Connection, session_id: str, source_sha256: str
) -> dict | None:
    row = conn.execute(
        """
        SELECT * FROM bill_draft
        WHERE session_id = ? AND source_sha256 = ? AND status != 'failed'
        ORDER BY created_at DESC LIMIT 1
        """,
        (session_id, source_sha256),
    ).fetchone()
    return draft_record(row) if row else None


def set_draft_status(
    conn: sqlite3.Connection,
    draft_id: str,
    status: str,
    *,
    backend: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE bill_draft
        SET status = ?, extractor_backend = COALESCE(?, extractor_backend),
            error = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, backend, error, _now(), draft_id),
    )
    conn.commit()


def save_draft_data(
    conn: sqlite3.Connection,
    draft_id: str,
    data: BillDraftData,
    *,
    backend: str | None = None,
) -> dict:
    calculation = validate_bill(data)
    status = "ready_for_review" if calculation.is_ready else "needs_information"
    cursor = conn.execute(
        """
        UPDATE bill_draft
        SET status = ?, extractor_backend = COALESCE(?, extractor_backend),
            data_json = ?, calculation_json = ?, error = NULL, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            backend,
            data.model_dump_json(),
            calculation.model_dump_json(),
            _now(),
            draft_id,
        ),
    )
    if cursor.rowcount == 0:
        raise KeyError(f"bill draft {draft_id} not found")
    conn.commit()
    return get_draft(conn, draft_id)


def draft_record(row: sqlite3.Row) -> dict:
    data_raw = row["data_json"] or "{}"
    calculation_raw = row["calculation_json"] or "{}"
    try:
        data = BillDraftData.model_validate_json(data_raw).model_dump(mode="json")
    except Exception:
        data = {}
    try:
        calculation = BillCalculation.model_validate_json(calculation_raw).model_dump(
            mode="json"
        )
    except Exception:
        calculation = {}
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "status": row["status"],
        "source_filename": row["source_filename"],
        "source_mime": row["source_mime"],
        "extractor_backend": row["extractor_backend"],
        "data": data,
        "calculation": calculation,
        "error": row["error"],
        "bill_id": row["bill_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_draft(conn: sqlite3.Connection, draft_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM bill_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"bill draft {draft_id} not found")
    return draft_record(row)


def list_drafts(
    conn: sqlite3.Connection, session_id: str | None = None, limit: int = 25
) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    if session_id:
        rows = conn.execute(
            """
            SELECT * FROM bill_draft WHERE session_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bill_draft ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [draft_record(row) for row in rows]


def _party_for_bill(
    conn: sqlite3.Connection, data: BillDraftData
) -> sqlite3.Row:
    name = (data.party.name or "").strip()
    party = conn.execute(
        "SELECT * FROM party WHERE lower(name) = lower(?) ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    normalized_phone = None
    if data.party.phone:
        normalized_phone = tools.normalize_phone(data.party.phone)
        if normalized_phone is None:
            raise ValueError("party phone must be a valid 10-digit Indian mobile")
    if party is None:
        party_type = "customer" if data.bill_type == "sale" else "supplier"
        cursor = conn.execute(
            "INSERT INTO party (name, type, phone, created_at) VALUES (?, ?, ?, ?)",
            (name, party_type, normalized_phone, _now()),
        )
        party = conn.execute(
            "SELECT * FROM party WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    elif normalized_phone and party["phone"] != normalized_phone:
        conn.execute(
            "UPDATE party SET phone = ? WHERE id = ?",
            (normalized_phone, int(party["id"])),
        )
        party = conn.execute(
            "SELECT * FROM party WHERE id = ?", (party["id"],)
        ).fetchone()
    assert party is not None
    return party


def _quantity(raw: str | None) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid item quantity {raw!r}") from exc
    if value <= 0:
        raise ValueError("item quantities must be positive")
    return value


def _quantity_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def finalize_draft(conn: sqlite3.Connection, draft_id: str) -> dict:
    """Finalize once and post every side effect in one SQLite transaction."""
    existing = conn.execute(
        "SELECT bill_id, status FROM bill_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    if existing is None:
        raise KeyError(f"bill draft {draft_id} not found")
    if existing["bill_id"] is not None:
        return get_bill(conn, int(existing["bill_id"]))

    row = conn.execute(
        "SELECT data_json FROM bill_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    data = BillDraftData.model_validate_json(row["data_json"])
    calculation = validate_bill(data)
    if not calculation.is_ready:
        detail = {
            "missing_fields": calculation.missing_fields,
            "warnings": [w.model_dump(mode="json") for w in calculation.warnings],
        }
        raise ValueError(f"bill draft is not ready: {json.dumps(detail)}")

    try:
        conn.execute("BEGIN IMMEDIATE")
        locked = conn.execute(
            "SELECT bill_id FROM bill_draft WHERE id = ?", (draft_id,)
        ).fetchone()
        if locked["bill_id"] is not None:
            conn.rollback()
            return get_bill(conn, int(locked["bill_id"]))

        conn.execute(
            "UPDATE bill_draft SET status = 'posting', updated_at = ? WHERE id = ?",
            (_now(), draft_id),
        )
        party = _party_for_bill(conn, data)
        bill_number = (data.bill_number or "").strip() or (
            f"DB-{'S' if data.bill_type == 'sale' else 'P'}-{draft_id[:8].upper()}"
        )
        cursor = conn.execute(
            """
            INSERT INTO bill (
                draft_id, type, bill_number, bill_date, party_id, party_name,
                party_phone, gstin, gst_mode, tax_scheme, gst_rate,
                payment_status, subtotal_paise, discount_paise,
                extra_charge_paise, taxable_paise, cgst_paise, sgst_paise,
                igst_paise, round_off_paise, grand_total_paise, paid_paise,
                note, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                draft_id,
                data.bill_type,
                bill_number,
                data.bill_date,
                int(party["id"]),
                party["name"],
                party["phone"],
                data.party.gstin,
                data.gst_mode,
                data.tax_scheme if data.gst_mode == "gst" else None,
                data.gst_rate if data.gst_mode == "gst" else None,
                data.payment_status,
                calculation.subtotal_paise,
                calculation.discount_paise,
                calculation.extra_charge_paise,
                calculation.taxable_paise,
                calculation.cgst_paise,
                calculation.sgst_paise,
                calculation.igst_paise,
                calculation.round_off_paise,
                calculation.grand_total_paise,
                calculation.paid_paise,
                data.note,
                _now(),
            ),
        )
        bill_id = int(cursor.lastrowid)

        direction = "in" if data.bill_type == "purchase" else "out"
        for item, line in zip(data.items, calculation.lines, strict=True):
            assert item.name is not None
            assert item.unit_price_paise is not None
            assert line.calculated_total_paise is not None
            quantity = _quantity(item.quantity)
            conn.execute(
                """
                INSERT INTO bill_item (
                    bill_id, name, quantity, unit, unit_price_paise,
                    line_total_paise, written_total_paise, hsn, gst_rate
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_id,
                    item.name.strip(),
                    _quantity_text(quantity),
                    item.unit,
                    item.unit_price_paise,
                    line.calculated_total_paise,
                    item.written_total_paise,
                    item.hsn,
                    item.gst_rate or data.gst_rate,
                ),
            )
            product = conn.execute(
                "SELECT * FROM product WHERE name = ? COLLATE NOCASE",
                (item.name.strip(),),
            ).fetchone()
            if product is None:
                product_cursor = conn.execute(
                    """
                    INSERT INTO product (name, quantity, unit, updated_at)
                    VALUES (?, '0', ?, ?)
                    """,
                    (item.name.strip(), item.unit, _now()),
                )
                product_id = int(product_cursor.lastrowid)
                current_quantity = Decimal(0)
            else:
                product_id = int(product["id"])
                current_quantity = Decimal(str(product["quantity"]))
            new_quantity = (
                current_quantity + quantity
                if direction == "in"
                else current_quantity - quantity
            )
            conn.execute(
                "UPDATE product SET quantity = ?, unit = COALESCE(?, unit), "
                "updated_at = ? WHERE id = ?",
                (_quantity_text(new_quantity), item.unit, _now(), product_id),
            )
            conn.execute(
                """
                INSERT INTO stock_movement (
                    bill_id, product_id, direction, quantity, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    bill_id,
                    product_id,
                    direction,
                    _quantity_text(quantity),
                    _now(),
                ),
            )

        if calculation.paid_paise > 0:
            cash_direction = "in" if data.bill_type == "sale" else "out"
            conn.execute(
                """
                INSERT INTO cashbook_entry (
                    bill_id, direction, amount_paise, note, entry_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_id,
                    cash_direction,
                    calculation.paid_paise,
                    f"{data.bill_type.title()} bill {bill_number}",
                    data.bill_date,
                    _now(),
                ),
            )

        if calculation.due_paise > 0:
            txn_type = "credit" if data.bill_type == "sale" else "debit"
            conn.execute(
                """
                INSERT INTO "transaction" (
                    party_id, type, amount, note, txn_date, source
                ) VALUES (?, ?, ?, ?, ?, 'text')
                """,
                (
                    int(party["id"]),
                    txn_type,
                    calculation.due_paise / 100,
                    f"Bill {bill_number}",
                    _now(),
                ),
            )

        conn.execute(
            """
            UPDATE bill_draft
            SET status = 'finalized', bill_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (bill_id, _now(), draft_id),
        )
        conn.commit()
        return get_bill(conn, bill_id)
    except Exception:
        conn.rollback()
        raise


def get_bill(conn: sqlite3.Connection, bill_id: int) -> dict:
    row = conn.execute(
        """
        SELECT b.*, p.type AS party_type
        FROM bill b JOIN party p ON p.id = b.party_id
        WHERE b.id = ?
        """,
        (bill_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"bill {bill_id} not found")
    items = [
        dict(item)
        for item in conn.execute(
            "SELECT * FROM bill_item WHERE bill_id = ? ORDER BY id", (bill_id,)
        ).fetchall()
    ]
    out = dict(row)
    out["items"] = items
    out["total_rupees"] = out["grand_total_paise"] / 100
    out["paid_rupees"] = out["paid_paise"] / 100
    out["due_rupees"] = (
        out["grand_total_paise"] - out["paid_paise"]
    ) / 100
    return out


def list_bills(
    conn: sqlite3.Connection, bill_type: str | None = None, limit: int = 100
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    sql = (
        "SELECT id, type, bill_number, bill_date, party_name, payment_status, "
        "grand_total_paise, paid_paise, gst_mode, created_at FROM bill "
    )
    params: tuple = ()
    if bill_type:
        sql += "WHERE type = ? "
        params = (bill_type,)
    sql += "ORDER BY bill_date DESC, id DESC LIMIT ?"
    rows = conn.execute(sql, (*params, limit)).fetchall()
    return [
        {
            **dict(row),
            "total_rupees": row["grand_total_paise"] / 100,
            "due_rupees": (
                row["grand_total_paise"] - row["paid_paise"]
            ) / 100,
        }
        for row in rows
    ]


def bill_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN type = 'sale' THEN grand_total_paise END), 0)
                AS total_sales_paise,
            COALESCE(SUM(CASE WHEN type = 'purchase' THEN grand_total_paise END), 0)
                AS total_purchases_paise
        FROM bill
        """
    ).fetchone()
    cash = conn.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN direction = 'in' THEN amount_paise END), 0)
                AS total_in_paise,
            COALESCE(SUM(CASE WHEN direction = 'out' THEN amount_paise END), 0)
                AS total_out_paise
        FROM cashbook_entry
        """
    ).fetchone()
    return {**dict(row), **dict(cash)}


def list_stock(conn: sqlite3.Connection) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT id, name, quantity, unit, updated_at FROM product ORDER BY name"
        ).fetchall()
    ]


def list_cashbook(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    rows = conn.execute(
        """
        SELECT c.*, b.bill_number, b.type AS bill_type, b.party_name
        FROM cashbook_entry c
        LEFT JOIN bill b ON b.id = c.bill_id
        ORDER BY entry_date DESC, c.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {**dict(row), "amount_rupees": row["amount_paise"] / 100}
        for row in rows
    ]
