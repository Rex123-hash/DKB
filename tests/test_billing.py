"""Deterministic tests for the AI billing domain (no LLM assertions)."""
from __future__ import annotations

import hashlib
from datetime import date
import re

import pytest

from app import db, demo_data
from app.billing import repository, service
from app.billing.calculator import validate_bill
from app.billing.extractors import (
    FakeBillExtractor,
    OllamaBillExtractor,
    remove_empty_items,
)
from app.billing.models import BillDraftData, BillItemDraft, PartyDraft


def _draft(**overrides) -> BillDraftData:
    data = {
        "bill_type": "sale",
        "bill_number": "S-001",
        "bill_date": "2026-07-29",
        "party": {"name": "Ramesh", "phone": "9876543210"},
        "gst_mode": "non_gst",
        "items": [
            {
                "name": "Rice",
                "quantity": "2",
                "unit": "kg",
                "unit_price_paise": 5000,
                "written_total_paise": 10000,
            }
        ],
        "payment_status": "paid",
    }
    data.update(overrides)
    return BillDraftData.model_validate(data)


def _conn():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


def _saved_draft(conn, data: BillDraftData) -> str:
    draft_id = repository.create_draft(
        conn,
        session_id="test",
        source_filename="bill.jpg",
        source_mime="image/jpeg",
        source_path="bill.jpg",
        source_sha256="abc",
    )
    repository.save_draft_data(conn, draft_id, data, backend="fake")
    return draft_id


def test_non_gst_bill_math():
    calc = validate_bill(_draft())
    assert calc.is_ready
    assert calc.subtotal_paise == 10000
    assert calc.gst_paise == 0
    assert calc.grand_total_paise == 10000
    assert calc.paid_paise == 10000
    assert calc.due_paise == 0


def test_gst_bill_splits_cgst_sgst():
    calc = validate_bill(
        _draft(gst_mode="gst", gst_rate="18", tax_scheme="cgst_sgst")
    )
    assert calc.is_ready
    assert calc.gst_paise == 1800
    assert calc.cgst_paise == 900
    assert calc.sgst_paise == 900
    assert calc.igst_paise == 0
    assert calc.grand_total_paise == 11800


def test_discount_extra_charge_gst_and_roundoff_are_deterministic():
    calc = validate_bill(
        _draft(
            gst_mode="gst",
            gst_rate="10",
            tax_scheme="igst",
            discount_paise=1000,
            extra_charge_paise=500,
            round_off_paise=50,
        )
    )
    assert calc.subtotal_paise == 10000
    assert calc.taxable_paise == 9500
    assert calc.igst_paise == 950
    assert calc.grand_total_paise == 10500


def test_math_disagreement_blocks_confirmation():
    data = _draft()
    data.items[0].written_total_paise = 9000
    calc = validate_bill(data)
    assert not calc.is_ready
    assert calc.warnings[0].code == "line_total_mismatch"
    assert calc.warnings[0].severity == "error"


def test_missing_fields_are_explicit():
    calc = validate_bill(BillDraftData())
    assert {
        "bill_type",
        "bill_date",
        "party.name",
        "gst_mode",
        "payment_status",
        "items",
    }.issubset(set(calc.missing_fields))


def test_non_bill_image_is_blocked_before_accounting():
    calc = validate_bill(
        BillDraftData(
            document_kind="not_bill",
            document_reason="This is a stock summary screen.",
            items=[
                BillItemDraft(
                    name="A product copied from the stock screen",
                    quantity="12",
                    unit_price_paise=500,
                )
            ],
        )
    )
    assert not calc.is_ready
    assert any(w.code == "not_a_bill" for w in calc.warnings)
    assert calc.lines == []
    assert calc.missing_fields == []


def test_non_bill_provider_rows_are_discarded():
    cleaned = remove_empty_items(
        BillDraftData(
            document_kind="not_bill",
            document_reason="This is an inventory report.",
            bill_type="sale",
            party=PartyDraft(name="Hallucinated party"),
            items=[
                BillItemDraft(
                    name="Real inventory row but not a transaction",
                    quantity="5",
                    unit_price_paise=1000,
                )
            ],
        )
    )

    assert cleaned.document_kind == "not_bill"
    assert cleaned.document_reason == "This is an inventory report."
    assert cleaned.bill_type is None
    assert cleaned.party.name is None
    assert cleaned.items == []


def test_completely_empty_extracted_rows_are_removed():
    cleaned = remove_empty_items(
        BillDraftData(
            items=[
                {},
                {"name": "Rice", "quantity": "2", "unit_price_paise": 5000},
                {},
            ]
        )
    )
    assert len(cleaned.items) == 1
    assert cleaned.items[0].name == "Rice"


def test_multiple_missing_quantities_become_one_question_and_auto_total():
    data = _draft(
        items=[
            {"name": "Book A", "quantity": None, "unit_price_paise": 6000},
            {"name": "Book B", "quantity": None, "unit_price_paise": 3500},
        ]
    )
    before = validate_bill(data)
    assert "items.quantities" in before.missing_fields
    assert not any(re.match(r"items\.\d+\.quantity", p) for p in before.missing_fields)

    updated = FakeBillExtractor().refine(data, "yes, all are 1")
    after = validate_bill(updated)

    assert [item.quantity for item in updated.items] == ["1", "1"]
    assert after.subtotal_paise == 9500
    assert after.grand_total_paise == 9500


def test_visible_line_amounts_are_summed_while_rate_needs_confirmation():
    data = _draft(
        items=[
            {
                "name": "Handwritten book",
                "quantity": "14",
                "unit_price_paise": None,
                "written_total_paise": 19000,
            }
        ],
        written_grand_total_paise=19000,
    )

    calc = validate_bill(data)

    assert calc.subtotal_paise == 19000
    assert calc.grand_total_paise == 19000
    assert calc.missing_fields == ["items.0.unit_price_paise"]
    assert not any(w.code == "grand_total_mismatch" for w in calc.warnings)


def test_extractor_normalizes_visible_dates_and_preserves_integer_quantities():
    cleaned = remove_empty_items(
        BillDraftData(
            bill_date="12/5/07",
            items=[
                {
                    "name": "Books",
                    "quantity": "10",
                    "written_total_paise": 50000,
                }
            ],
        )
    )

    assert cleaned.bill_date == "2007-05-12"
    assert cleaned.items[0].quantity == "10"
    assert cleaned.items[0].unit_price_paise == 5000


def test_low_confidence_handwriting_is_shown_for_human_review():
    calc = validate_bill(
        _draft(confidence={"bill_date": 0.4})
    )
    warning = next(w for w in calc.warnings if w.code == "low_confidence")
    assert warning.field == "bill_date"
    assert warning.severity == "warning"


def test_ollama_common_clarification_is_deterministic_without_network():
    updated = OllamaBillExtractor().refine(
        BillDraftData(), "This is a purchase bill"
    )
    assert updated.bill_type == "purchase"


def test_fake_chat_clarifications_follow_the_assistant_question_order():
    extractor = FakeBillExtractor()
    draft = BillDraftData()
    for answer in (
        "purchase",
        "2026-07-29",
        "Supplier name is Verma Traders",
        "non gst",
        "paid",
    ):
        draft = extractor.refine(draft, answer)
    assert draft.bill_type == "purchase"
    assert draft.bill_date == "2026-07-29"
    assert draft.party.name == "Verma Traders"
    assert draft.gst_mode == "non_gst"
    assert draft.payment_status == "paid"


def test_paid_purchase_increases_stock_and_posts_cash_out():
    conn = _conn()
    data = _draft(
        bill_type="purchase",
        bill_number="P-001",
        party={"name": "Verma Traders"},
    )
    draft_id = _saved_draft(conn, data)
    bill = repository.finalize_draft(conn, draft_id)

    assert bill["type"] == "purchase"
    assert repository.list_stock(conn)[0]["quantity"] == "2"
    cash = repository.list_cashbook(conn)
    assert len(cash) == 1
    assert cash[0]["direction"] == "out"
    assert cash[0]["amount_paise"] == 10000
    party = db.find_party_by_name(conn, "Verma Traders")
    assert party["type"] == "supplier"
    assert db.get_balance(conn, party["id"]) == 0


def test_credit_sale_decreases_stock_and_creates_receivable():
    conn = _conn()
    data = _draft(payment_status="credit")
    draft_id = _saved_draft(conn, data)
    repository.finalize_draft(conn, draft_id)

    assert repository.list_stock(conn)[0]["quantity"] == "-2"
    assert repository.list_cashbook(conn) == []
    party = db.find_party_by_name(conn, "Ramesh")
    assert db.get_balance(conn, party["id"]) == 100.0


def test_partial_purchase_posts_paid_cash_and_supplier_due():
    conn = _conn()
    data = _draft(
        bill_type="purchase",
        party={"name": "Verma Traders"},
        payment_status="partial",
        paid_amount_paise=4000,
    )
    draft_id = _saved_draft(conn, data)
    repository.finalize_draft(conn, draft_id)

    cash = repository.list_cashbook(conn)
    assert cash[0]["amount_paise"] == 4000
    party = db.find_party_by_name(conn, "Verma Traders")
    assert db.get_balance(conn, party["id"]) == -60.0


def test_confirmation_is_idempotent():
    conn = _conn()
    draft_id = _saved_draft(conn, _draft())
    first = repository.finalize_draft(conn, draft_id)
    second = repository.finalize_draft(conn, draft_id)

    assert first["id"] == second["id"]
    assert conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM bill_item").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 1


def test_failed_finalization_rolls_back_every_side_effect():
    conn = _conn()
    data = _draft(party={"name": "Invalid Phone", "phone": "123"})
    draft_id = _saved_draft(conn, data)

    with pytest.raises(ValueError, match="valid 10-digit"):
        repository.finalize_draft(conn, draft_id)

    assert conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM product").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 0
    assert repository.get_draft(conn, draft_id)["status"] == "ready_for_review"


def test_demo_reset_clears_billing_data_without_foreign_key_errors():
    conn = _conn()
    draft_id = _saved_draft(conn, _draft())
    repository.finalize_draft(conn, draft_id)

    demo_data.reset(conn)

    assert conn.execute("SELECT COUNT(*) FROM bill").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM bill_draft").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM party").fetchone()[0] == 0


def test_fake_scan_is_persistent_and_duplicate_safe(tmp_path, monkeypatch):
    conn = _conn()
    monkeypatch.setenv("DUKANBOOK_SCAN_DIR", str(tmp_path))
    extractor = FakeBillExtractor(_draft())
    image = b"\xff\xd8\xffnot-a-complete-jpeg-but-valid-test-signature"

    first = service.scan_bill(
        conn,
        image_bytes=image,
        filename="handwritten.jpg",
        mime_type="image/jpeg",
        session_id="shop-1",
        extractor=extractor,
    )
    second = service.scan_bill(
        conn,
        image_bytes=image,
        filename="handwritten.jpg",
        mime_type="image/jpeg",
        session_id="shop-1",
        extractor=extractor,
    )

    assert first["status"] == "ready_for_review"
    assert first["duplicate"] is False
    assert second["id"] == first["id"]
    assert second["duplicate"] is True
    assert len(list(tmp_path.iterdir())) == 1


def test_cached_non_bill_is_normalized_without_calling_ai_again(
    tmp_path, monkeypatch
):
    conn = _conn()
    monkeypatch.setenv("DUKANBOOK_SCAN_DIR", str(tmp_path))
    image = b"\xff\xd8\xfflegacy-stock-screen"
    draft_id = repository.create_draft(
        conn,
        session_id="shop-legacy",
        source_filename="stock.jpg",
        source_mime="image/jpeg",
        source_path=str(tmp_path / "legacy.jpg"),
        source_sha256=hashlib.sha256(image).hexdigest(),
    )
    repository.save_draft_data(
        conn,
        draft_id,
        BillDraftData(
            document_kind="not_bill",
            document_reason="Stock summary",
            bill_type="sale",
            items=[
                BillItemDraft(
                    name="Inventory row", quantity="5", unit_price_paise=1000
                )
            ],
        ),
    )

    result = service.scan_bill(
        conn,
        image_bytes=image,
        filename="stock.jpg",
        mime_type="image/jpeg",
        session_id="shop-legacy",
        extractor=FakeBillExtractor(),
    )

    assert result["duplicate"] is True
    assert result["data"]["document_kind"] == "not_bill"
    assert result["data"]["items"] == []
    assert result["calculation"]["missing_fields"] == []


def test_none_answer_removes_phantom_items_instead_of_looping():
    conn = _conn()
    data = _draft(items=[{}, {}, {}])
    draft_id = _saved_draft(conn, data)

    updated = service.answer_draft(
        conn, draft_id, "NO MISSING ITEM", extractor=FakeBillExtractor()
    )

    assert updated["data"]["items"] == []
    assert "items" in updated["calculation"]["missing_fields"]
    assert not any(
        path.startswith("items.") for path in updated["calculation"]["missing_fields"]
    )


def test_one_message_with_every_detail_is_applied_at_once():
    """A shopkeeper who says everything once must not be asked again."""
    draft = FakeBillExtractor().refine(
        BillDraftData(document_kind="bill"),
        "purchase bill from Verma Traders, date 2026-01-03, non-GST, cash paid",
    )
    assert draft.bill_type == "purchase"
    assert draft.party.name == "Verma Traders"
    assert draft.bill_date == "2026-01-03"
    assert draft.gst_mode == "non_gst"
    assert draft.payment_status == "paid"


def test_multi_detail_answer_does_not_swallow_the_sentence_as_a_party_name():
    draft = FakeBillExtractor().refine(
        BillDraftData(document_kind="bill"), "sale bill, 18% GST, IGST, udhaar"
    )
    assert draft.bill_type == "sale"
    assert draft.gst_mode == "gst"
    assert draft.gst_rate == "18"
    assert draft.tax_scheme == "igst"
    assert draft.payment_status == "credit"
    assert draft.party.name is None


def test_refiner_never_overwrites_a_value_already_on_the_draft():
    draft = FakeBillExtractor().refine(
        _draft(party={"name": "Ramesh"}), "sale to Suresh"
    )
    assert draft.party.name == "Ramesh"


def test_indian_numeric_date_in_a_sentence_is_understood():
    draft = FakeBillExtractor().refine(
        BillDraftData(document_kind="bill"), "purchase, 03/01/2026, paid"
    )
    assert draft.bill_date == "2026-01-03"


def test_ledger_entry_is_dated_on_the_bill_date_not_today():
    conn = _conn()
    data = _draft(bill_type="sale", payment_status="credit", bill_date="2026-01-05")
    repository.finalize_draft(conn, _saved_draft(conn, data))
    txn_date = conn.execute('SELECT txn_date FROM "transaction"').fetchone()[0]
    assert txn_date.startswith("2026-01-05")


def test_sale_beyond_available_stock_reports_a_negative_stock_alert():
    conn = _conn()
    bill = repository.finalize_draft(
        conn, _saved_draft(conn, _draft(bill_type="sale"))
    )
    assert any("Rice" in alert for alert in bill["stock_alerts"])


def _finalized(conn, **overrides):
    return repository.finalize_draft(conn, _saved_draft(conn, _draft(**overrides)))


def test_invoice_totals_ladder_adds_up_to_the_grand_total():
    """Every printed money row must reconcile with the amount due."""
    from app.billing.pdf import build_totals_rows

    conn = _conn()
    bill = _finalized(
        conn,
        gst_mode="gst",
        gst_rate="18",
        tax_scheme="cgst_sgst",
        discount_paise=1000,
        extra_charge_paise=500,
        round_off_paise=-47,
        items=[
            {"name": "Rice", "quantity": "2", "unit": "kg",
             "unit_price_paise": 5000, "written_total_paise": 10000}
        ],
    )
    rows = build_totals_rows(bill)
    ladder = sum(row["paise"] for row in rows if row["kind"] == "ladder")
    grand = next(row for row in rows if row["kind"] == "grand")
    assert ladder == grand["paise"] == bill["grand_total_paise"]

    labels = [row["label"] for row in rows]
    assert "Discount" in labels and "Round off" in labels
    assert any(label.startswith("CGST") for label in labels)
    assert any(label.startswith("SGST") for label in labels)


def test_invoice_line_totals_reconcile_with_the_printed_subtotal():
    from app.billing.pdf import build_totals_rows

    conn = _conn()
    bill = _finalized(conn, gst_mode="gst", gst_rate="18", tax_scheme="igst")
    rows = build_totals_rows(bill)
    subtotal = next(row for row in rows if row["label"] == "Net total")
    assert subtotal["paise"] == sum(
        item["line_total_paise"] for item in bill["items"]
    )


def test_credit_invoice_shows_the_outstanding_balance():
    from app.billing.pdf import build_totals_rows

    conn = _conn()
    bill = _finalized(conn, payment_status="credit")
    balance = next(row for row in build_totals_rows(bill) if row["label"] == "Balance due")
    assert balance["paise"] == bill["grand_total_paise"]


def test_invoice_pdf_renders_for_gst_and_non_gst_bills():
    from app.billing.pdf import build_bill_pdf

    conn = _conn()
    for overrides in (
        {"gst_mode": "non_gst"},
        {"gst_mode": "gst", "gst_rate": "12", "tax_scheme": "cgst_sgst"},
    ):
        pdf = build_bill_pdf(_finalized(_conn(), **overrides))
        assert pdf.startswith(b"%PDF") and len(pdf) > 2000


def test_amount_in_words_uses_indian_numbering():
    from app.billing.pdf import amount_in_words

    assert amount_in_words(12550) == "One Hundred Twenty Five Rupees and Fifty Paise Only"
    assert amount_in_words(10000000) == "One Lakh Rupees Only"


def test_seal_is_generated_per_party_and_stays_inside_the_ring():
    from app.billing.pdf import build_party_seal

    for name, phone in (
        ("Srishti", "9700480123"),
        ("Verma Traders & Sons", "9123456780"),
        ("Ramesh", None),
    ):
        seal = build_party_seal(name, phone, 27.0)
        texts = [
            shape.text
            for shape in seal.contents
            if getattr(shape, "text", None) is not None
        ]
        assert any(name.split()[0].upper() in text for text in texts)
        assert all(
            shape.x - 0.01 <= 27.0 and shape.y >= -0.01
            for shape in seal.contents
            if hasattr(shape, "x")
        )


def test_unrecognised_answer_is_reported_instead_of_silently_ignored():
    conn = _conn()
    draft_id = _saved_draft(conn, BillDraftData(document_kind="bill"))
    ignored = service.answer_draft(
        conn, draft_id, "hmm let me check the register", extractor=FakeBillExtractor()
    )
    assert ignored["answer_applied"] is False

    applied = service.answer_draft(
        conn, draft_id, "purchase bill", extractor=FakeBillExtractor()
    )
    assert applied["answer_applied"] is True


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("bill date 2026-01-18", "2026-01-18"),
        ("18/01/2026", "2026-01-18"),
        ("18-1-26", "2026-01-18"),
        ("18 January 2026", "2026-01-18"),
        ("18th Jan 2026", "2026-01-18"),
        ("Jan 18, 2026", "2026-01-18"),
        ("18 jan", "2026-01-18"),
        ("date 18 January 2026 hai", "2026-01-18"),
    ],
)
def test_dates_are_understood_in_the_way_shopkeepers_write_them(answer, expected):
    import app.billing.extractors as extractors

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 3)

    original = extractors.date
    extractors.date = _FixedDate
    try:
        draft = FakeBillExtractor().refine(BillDraftData(document_kind="bill"), answer)
    finally:
        extractors.date = original
    assert draft.bill_date == expected


def test_relative_day_words_are_understood():
    import app.billing.extractors as extractors

    class _FixedDate(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 3)

    original = extractors.date
    extractors.date = _FixedDate
    try:
        assert FakeBillExtractor().refine(
            BillDraftData(document_kind="bill"), "kal ka bill hai"
        ).bill_date == "2026-08-02"
        assert FakeBillExtractor().refine(
            BillDraftData(document_kind="bill"), "aaj"
        ).bill_date == "2026-08-03"
    finally:
        extractors.date = original
