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


def test_visible_tax_columns_settle_the_gst_mode_without_asking():
    from app.billing.extractors import remove_empty_items as clean

    draft = clean(BillDraftData(
        document_kind="bill", tax_scheme="cgst_sgst",
        items=[BillItemDraft(name="Rice", quantity="2", unit_price_paise=5000,
                             gst_rate="5")],
    ))
    assert draft.gst_mode == "gst"
    # One rate across every row is the bill's rate; nothing to ask.
    assert draft.gst_rate == "5"


def test_mixed_item_gst_rates_are_not_collapsed_into_one_rate():
    from app.billing.extractors import remove_empty_items as clean

    draft = clean(BillDraftData(
        document_kind="bill", tax_scheme="cgst_sgst",
        items=[
            BillItemDraft(name="Rice", quantity="2", unit_price_paise=5000, gst_rate="5"),
            BillItemDraft(name="Soap", quantity="1", unit_price_paise=3000, gst_rate="12"),
        ],
    ))
    assert draft.gst_mode == "gst"
    assert draft.gst_rate is None


def test_several_gst_rates_are_calculated_rather_than_refused():
    """Each row keeps its own rate; the bill is no longer blocked."""
    calc = validate_bill(_draft(
        gst_mode="gst", gst_rate=None, tax_scheme="cgst_sgst",
        items=[
            {"name": "Rice", "quantity": "2", "unit_price_paise": 5000, "gst_rate": "5"},
            {"name": "Soap", "quantity": "1", "unit_price_paise": 3000, "gst_rate": "12"},
        ],
    ))
    assert not [w for w in calc.warnings if w.severity == "error"]
    assert calc.gst_paise == 500 + 360          # 5% of 100.00, 12% of 30.00
    assert {line.rate for line in calc.tax_lines} == {"5", "12"}


def test_a_corrupted_phone_read_is_dropped_rather_than_stored():
    from app.billing.extractors import remove_empty_items as clean

    draft = clean(BillDraftData(
        document_kind="bill",
        party=PartyDraft(phone="+91 8282828281\u81ea\u4fe1GSTIN: 09AAACH7409R1ZZ"),
    ))
    assert draft.party.phone == "8282828281"
    assert draft.party.gstin is None


def test_same_rate_written_differently_is_not_treated_as_mixed():
    from app.billing.extractors import remove_empty_items as clean

    draft = clean(BillDraftData(
        document_kind="bill", tax_scheme="cgst_sgst",
        items=[
            BillItemDraft(name="Rice", quantity="2", unit_price_paise=5000, gst_rate="5.00"),
            BillItemDraft(name="Dal", quantity="1", unit_price_paise=3000, gst_rate="5"),
        ],
    ))
    assert draft.gst_rate == "5"
    assert not [w for w in validate_bill(draft).warnings if w.code == "mixed_gst_rates"]


def test_tax_inclusive_amount_column_is_not_reported_as_a_maths_error():
    """Printed GST invoices show the line amount with tax already added."""
    calc = validate_bill(_draft(
        gst_mode="gst", gst_rate="5", tax_scheme="cgst_sgst",
        items=[{"name": "Rice", "quantity": "30", "unit_price_paise": 40000,
                "written_total_paise": 1260000}],   # 12000 x 1.05
    ))
    assert not [w for w in calc.warnings if w.code == "line_total_mismatch"]
    # The taxable base still drives the accounting, not the inclusive figure.
    assert calc.subtotal_paise == 1200000


def test_a_genuine_line_error_is_still_caught_on_a_gst_bill():
    calc = validate_bill(_draft(
        gst_mode="gst", gst_rate="5", tax_scheme="cgst_sgst",
        items=[{"name": "Rice", "quantity": "30", "unit_price_paise": 40000,
                "written_total_paise": 900000}],
    ))
    assert [w for w in calc.warnings if w.code == "line_total_mismatch"]


def test_grand_total_is_not_disputed_while_the_gst_rate_is_unknown():
    calc = validate_bill(_draft(
        gst_mode="gst", gst_rate=None, tax_scheme="cgst_sgst",
        written_grand_total_paise=1325065,
        items=[{"name": "Rice", "quantity": "30", "unit_price_paise": 40000}],
    ))
    assert not [w for w in calc.warnings if w.code == "grand_total_mismatch"]


def test_each_line_is_taxed_at_its_own_gst_rate():
    """Real kirana bills mix 5%, 12% and 18% on one invoice."""
    calc = validate_bill(_draft(
        gst_mode="gst", tax_scheme="cgst_sgst",
        items=[
            {"name": "Rice", "quantity": "30", "unit_price_paise": 40000, "gst_rate": "5"},
            {"name": "Oil", "quantity": "3", "unit_price_paise": 18000, "gst_rate": "5"},
            {"name": "Toothpaste", "quantity": "1", "unit_price_paise": 10000, "gst_rate": "12"},
        ],
    ))
    assert not calc.missing_fields
    assert not [w for w in calc.warnings if w.severity == "error"]
    assert calc.subtotal_paise == 1264000                 # 12000 + 540 + 100
    # 5% on 12540 = 627.00, 12% on 100 = 12.00
    assert calc.gst_paise == 62700 + 1200
    assert calc.cgst_paise + calc.sgst_paise == calc.gst_paise
    assert calc.grand_total_paise == 1264000 + 63900


def test_tax_is_summarised_per_rate_for_the_invoice():
    calc = validate_bill(_draft(
        gst_mode="gst", tax_scheme="cgst_sgst",
        items=[
            {"name": "Rice", "quantity": "30", "unit_price_paise": 40000, "gst_rate": "5"},
            {"name": "Toothpaste", "quantity": "1", "unit_price_paise": 10000, "gst_rate": "12"},
        ],
    ))
    rates = {line.rate: line for line in calc.tax_lines}
    assert set(rates) == {"5", "12"}
    assert rates["5"].taxable_paise == 1200000
    assert rates["5"].cgst_paise + rates["5"].sgst_paise == 60000
    assert rates["12"].taxable_paise == 10000


def test_a_bill_discount_is_shared_across_rates_without_losing_a_paisa():
    calc = validate_bill(_draft(
        gst_mode="gst", tax_scheme="cgst_sgst", discount_paise=10000,
        items=[
            {"name": "Rice", "quantity": "1", "unit_price_paise": 100000, "gst_rate": "5"},
            {"name": "Soap", "quantity": "1", "unit_price_paise": 100000, "gst_rate": "12"},
        ],
    ))
    assert sum(line.taxable_paise for line in calc.tax_lines) == calc.taxable_paise
    assert sum(line.gst_paise for line in calc.tax_lines) == calc.gst_paise


def test_a_bill_level_rate_still_covers_rows_that_carry_none():
    calc = validate_bill(_draft(
        gst_mode="gst", gst_rate="18", tax_scheme="igst",
        items=[{"name": "Rice", "quantity": "2", "unit_price_paise": 50000}],
    ))
    assert calc.igst_paise == 18000 and calc.gst_paise == 18000


def test_a_rate_is_still_required_when_no_row_carries_one():
    calc = validate_bill(_draft(
        gst_mode="gst", gst_rate=None, tax_scheme="cgst_sgst",
        items=[{"name": "Rice", "quantity": "2", "unit_price_paise": 50000}],
    ))
    assert "gst_rate" in calc.missing_fields


def test_invoice_declares_each_gst_slab_separately():
    from app.billing.pdf import build_totals_rows, build_bill_pdf

    conn = _conn()
    bill = repository.finalize_draft(conn, _saved_draft(conn, _draft(
        gst_mode="gst", gst_rate=None, tax_scheme="cgst_sgst",
        items=[
            {"name": "Rice", "quantity": "30", "unit_price_paise": 40000, "gst_rate": "5"},
            {"name": "Toothpaste", "quantity": "1", "unit_price_paise": 10000, "gst_rate": "12"},
        ],
    )))
    rows = build_totals_rows(bill)
    labels = [row["label"] for row in rows]
    assert "CGST @ 2.5%" in labels and "CGST @ 6%" in labels
    ladder = sum(row["paise"] for row in rows if row["kind"] == "ladder")
    grand = next(row for row in rows if row["kind"] == "grand")
    assert ladder == grand["paise"] == bill["grand_total_paise"]
    assert build_bill_pdf(bill).startswith(b"%PDF")


def test_reuploading_a_bill_we_could_not_read_retries_instead_of_caching_it(tmp_path, monkeypatch):
    """A cached zero-item draft must never be served back as the final word."""
    monkeypatch.setenv("DUKANBOOK_SCAN_DIR", str(tmp_path))
    conn = _conn()
    image = b"\x89PNG\r\n\x1a\n" + b"0" * 64

    blind = FakeBillExtractor(BillDraftData(document_kind="bill"))
    first = service.scan_bill(conn, image_bytes=image, filename="b.png",
                              mime_type="image/png", session_id="s", extractor=blind)
    assert first["data"]["items"] == []

    seeing = FakeBillExtractor(_draft())
    second = service.scan_bill(conn, image_bytes=image, filename="b.png",
                               mime_type="image/png", session_id="s", extractor=seeing)
    assert second["id"] == first["id"] and second["reprocessed"] is True
    assert [item["name"] for item in second["data"]["items"]] == ["Rice"]


def test_a_retry_keeps_the_answers_the_shopkeeper_already_gave(tmp_path, monkeypatch):
    monkeypatch.setenv("DUKANBOOK_SCAN_DIR", str(tmp_path))
    conn = _conn()
    image = b"\x89PNG\r\n\x1a\n" + b"1" * 64

    blind = FakeBillExtractor(BillDraftData(document_kind="bill"))
    draft = service.scan_bill(conn, image_bytes=image, filename="b.png",
                              mime_type="image/png", session_id="s", extractor=blind)
    service.answer_draft(conn, draft["id"], "purchase bill", extractor=blind)

    # The re-read sees a sale; the shopkeeper already said purchase.
    seeing = FakeBillExtractor(_draft(bill_type="sale", party={"name": "Someone Else"}))
    again = service.scan_bill(conn, image_bytes=image, filename="b.png",
                              mime_type="image/png", session_id="s", extractor=seeing)
    assert again["data"]["bill_type"] == "purchase"
    assert [item["name"] for item in again["data"]["items"]] == ["Rice"]


def test_amounts_written_with_symbols_or_blanks_survive_the_item_reader():
    """The reader must tolerate blank cells and rupee symbols, not crash."""
    from app.billing.extractors import BillItemsRead, items_from_read

    items = items_from_read(BillItemsRead.model_validate({"items": [
        {"name": "pencil", "quantity": "5", "unit": "", "unit_price_rupees": "",
         "amount_rupees": "\u20b950", "hsn": "", "gst_rate": ""},
        {"name": "pen", "quantity": "10", "unit": "pcs", "unit_price_rupees": "Rs 1,200.50",
         "amount_rupees": "", "hsn": "9608", "gst_rate": "12"},
    ]}))
    assert items[0].written_total_paise == 5000 and items[0].unit_price_paise is None
    assert items[1].unit_price_paise == 120050 and items[1].gst_rate == "12"


def test_faint_handwriting_money_pass_fills_rates_and_amounts_by_row():
    from app.billing.extractors import BillMoneyRead, apply_money_read

    draft = BillDraftData(
        document_kind="bill",
        written_grand_total_paise=None,
        items=[
            BillItemDraft(name="Rice", quantity="50"),
            BillItemDraft(
                name="Rai", quantity="2", unit_price_paise=6000
            ),
            BillItemDraft(name="Coconut oil", quantity="20100"),
        ],
    )
    read = BillMoneyRead.model_validate({
        "items": [
            {
                "row_number": 1,
                "quantity": "50 kg",
                "unit_price_rupees": "39",
                "amount_rupees": "1,950",
            },
            {
                "row_number": 2,
                "quantity": "2",
                "unit_price_rupees": "999",
                "amount_rupees": "120",
            },
            {
                "row_number": 3,
                "quantity": "20",
                "unit_price_rupees": "35",
                "amount_rupees": "700",
            },
        ],
        "written_grand_total_rupees": "2,070",
    })

    apply_money_read(draft, read)

    assert draft.items[0].unit_price_paise == 3900
    assert draft.items[0].written_total_paise == 195000
    assert draft.items[1].unit_price_paise == 6000  # first read is authoritative
    assert draft.items[1].written_total_paise == 12000
    assert draft.items[2].quantity == "20"  # "20 x 100 ml" is not 20100
    assert draft.items[2].unit_price_paise == 3500
    assert draft.written_grand_total_paise == 207000


def test_money_read_enhancement_upscales_a_small_scan_losslessly():
    from io import BytesIO
    from PIL import Image
    from app.billing.extractors import enhance_for_money_read

    original = BytesIO()
    Image.new("RGB", (320, 640), "#d8d8cf").save(original, format="JPEG")
    enhanced, mime = enhance_for_money_read(original.getvalue(), "image/jpeg")

    with Image.open(BytesIO(enhanced)) as image:
        assert mime == "image/png"
        assert image.mode == "L"
        assert max(image.size) == 1920


def test_terms_read_fills_only_what_the_bill_actually_says():
    from app.billing.extractors import BillTermsRead, _apply_terms

    draft = BillDraftData(document_kind="bill", bill_type="sale")
    _apply_terms(draft, BillTermsRead(
        bill_type="purchase", gst_mode="gst", tax_scheme="",
        gst_rate="18.00", payment_status="udhaar",
    ))
    assert draft.bill_type == "sale"          # already known, never overwritten
    assert draft.gst_mode == "gst"
    assert draft.gst_rate == "18"
    assert draft.tax_scheme is None           # blank stays blank
    assert draft.payment_status is None       # not a permitted value, so ignored


def test_a_heavy_screenshot_is_re_encoded_before_every_provider_call():
    from io import BytesIO
    from PIL import Image
    from app.billing.service import prepare_for_vision

    # Small enough not to need resizing, but noisy enough to be a heavy PNG.
    import random
    random.seed(0)
    image = Image.new("RGB", (950, 760))
    image.putdata([(random.randrange(256),) * 3 for _ in range(950 * 760)])
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    heavy = buffer.getvalue()
    assert len(heavy) > 400 * 1024

    prepared, mime = prepare_for_vision(heavy, "image/png")
    assert mime == "image/jpeg" and len(prepared) < len(heavy)


def test_a_small_image_is_sent_exactly_as_the_shopkeeper_supplied_it():
    from io import BytesIO
    from PIL import Image
    from app.billing.service import prepare_for_vision

    buffer = BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")
    original = buffer.getvalue()
    assert prepare_for_vision(original, "image/png") == (original, "image/png")


def test_a_clarification_never_wipes_the_bill_it_was_answering():
    """The refine schema returns partial objects; a merge must protect the draft."""
    from app.billing.extractors import merge_refinement

    current = _draft(bill_type="purchase", gst_mode="gst", gst_rate="5")
    partial = BillDraftData(document_kind="bill", payment_status="credit")

    merged = merge_refinement(current, partial)
    assert merged.payment_status == "credit"          # the correction applies
    assert merged.bill_type == "purchase"             # everything else survives
    assert merged.party.name == "Ramesh"
    assert [item.name for item in merged.items] == ["Rice"]
    assert merged.gst_rate == "5"


def test_a_clarification_that_supplies_items_does_replace_them():
    from app.billing.extractors import merge_refinement

    merged = merge_refinement(
        _draft(),
        BillDraftData(document_kind="bill", items=[
            BillItemDraft(name="Dal", quantity="3", unit_price_paise=7000)
        ]),
    )
    assert [item.name for item in merged.items] == ["Dal"]


def test_deleting_a_bill_puts_the_stock_back():
    conn = _conn()
    bill = repository.finalize_draft(conn, _saved_draft(conn, _draft(
        bill_type="purchase", payment_status="paid",
        items=[{"name": "Rice", "quantity": "8", "unit": "kg",
                "unit_price_paise": 5000, "written_total_paise": 40000}],
    )))
    assert conn.execute("SELECT quantity FROM product WHERE name='Rice'").fetchone()[0] == "8"

    repository.delete_bill(conn, bill["id"])

    assert conn.execute("SELECT quantity FROM product WHERE name='Rice'").fetchone()[0] == "0"
    assert conn.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0] == 0


def test_deleting_a_bill_removes_its_cashbook_and_ledger_entries():
    conn = _conn()
    paid = repository.finalize_draft(conn, _saved_draft(conn, _draft(payment_status="paid")))
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 1
    repository.delete_bill(conn, paid["id"])
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 0

    credit = repository.finalize_draft(conn, _saved_draft(conn, _draft(payment_status="credit")))
    assert conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0] == 1
    repository.delete_bill(conn, credit["id"])
    assert conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0] == 0


def test_deleting_a_bill_leaves_no_orphan_rows_or_draft():
    conn = _conn()
    draft_id = _saved_draft(conn, _draft())
    bill = repository.finalize_draft(conn, draft_id)
    repository.delete_bill(conn, bill["id"])

    for table in ("bill", "bill_item", "stock_movement", "cashbook_entry"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    # The draft must become re-usable, not stay stuck as a finalized shell.
    row = conn.execute(
        "SELECT status, bill_id FROM bill_draft WHERE id = ?", (draft_id,)
    ).fetchone()
    assert row["bill_id"] is None and row["status"] != "finalized"


def test_deleting_one_bill_does_not_disturb_another():
    conn = _conn()
    keep = repository.finalize_draft(conn, _saved_draft(conn, _draft(
        bill_number="KEEP-1", payment_status="credit")))
    drop = repository.finalize_draft(conn, _saved_draft(conn, _draft(
        bill_number="DROP-1", payment_status="credit")))

    repository.delete_bill(conn, drop["id"])

    assert [r["bill_number"] for r in conn.execute("SELECT bill_number FROM bill")] == ["KEEP-1"]
    notes = [r["note"] for r in conn.execute('SELECT note FROM "transaction"')]
    assert notes == ["Bill KEEP-1"]
    assert repository.get_bill(conn, keep["id"])["grand_total_paise"] == keep["grand_total_paise"]


def test_deleting_a_missing_bill_is_reported_not_silently_ignored():
    conn = _conn()
    with pytest.raises(KeyError):
        repository.delete_bill(conn, 9999)


def test_editing_a_bill_moves_the_stock_by_the_difference_only():
    conn = _conn()
    bill = repository.finalize_draft(conn, _saved_draft(conn, _draft(
        bill_type="purchase", payment_status="paid",
        items=[{"name": "Rice", "quantity": "10", "unit": "kg",
                "unit_price_paise": 5000, "written_total_paise": 50000}],
    )))
    assert conn.execute("SELECT quantity FROM product WHERE name='Rice'").fetchone()[0] == "10"

    corrected = _draft(
        bill_type="purchase", payment_status="paid",
        items=[{"name": "Rice", "quantity": "4", "unit": "kg",
                "unit_price_paise": 5000, "written_total_paise": 20000}],
    )
    updated = repository.update_bill(conn, bill["id"], corrected)

    assert updated["id"] == bill["id"]              # identity is kept
    assert updated["grand_total_paise"] == 20000
    assert conn.execute("SELECT quantity FROM product WHERE name='Rice'").fetchone()[0] == "4"
    assert conn.execute("SELECT COUNT(*) FROM stock_movement").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM bill_item").fetchone()[0] == 1


def test_editing_a_bill_re_posts_cash_and_ledger_once():
    conn = _conn()
    bill = repository.finalize_draft(conn, _saved_draft(conn, _draft(payment_status="paid")))
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 1
    assert conn.execute('SELECT COUNT(*) FROM "transaction"').fetchone()[0] == 0

    # Paid becomes credit: the cash entry must go, a receivable must appear.
    repository.update_bill(conn, bill["id"], _draft(payment_status="credit"))

    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 0
    ledger = conn.execute('SELECT type, amount FROM "transaction"').fetchall()
    assert len(ledger) == 1 and ledger[0]["type"] == "credit"


def test_an_edit_that_does_not_add_up_is_refused_and_changes_nothing():
    conn = _conn()
    bill = repository.finalize_draft(conn, _saved_draft(conn, _draft(payment_status="paid")))
    broken = _draft(payment_status=None)          # missing a required answer

    with pytest.raises(ValueError):
        repository.update_bill(conn, bill["id"], broken)

    # The original posting must survive an refused edit untouched.
    assert repository.get_bill(conn, bill["id"])["payment_status"] == "paid"
    assert conn.execute("SELECT quantity FROM product WHERE name='Rice'").fetchone()[0] == "-2"
    assert conn.execute("SELECT COUNT(*) FROM cashbook_entry").fetchone()[0] == 1


def test_editing_a_missing_bill_is_reported():
    conn = _conn()
    with pytest.raises(KeyError):
        repository.update_bill(conn, 4242, _draft())
