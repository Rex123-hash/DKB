"""Deterministic bill mathematics and validation.

The extractor is allowed to read numbers. It is never trusted to calculate
them. All final amounts are derived here using Decimal and integer paise.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.billing.models import (
    BillCalculation,
    BillDraftData,
    DraftWarning,
    LineCalculation,
)

_PAISE = Decimal("1")
_LOW_CONFIDENCE = 0.65


def _decimal(raw: str | int | float | Decimal | None) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _round_paise(value: Decimal) -> int:
    return int(value.quantize(_PAISE, rounding=ROUND_HALF_UP))


def _matches_tax_inclusive(draft: BillDraftData, item, line_total: int) -> bool:
    """Is the written line amount simply the taxable amount plus its GST?

    Most printed Indian invoices put the tax-inclusive figure in the Amount
    column while quantity x rate is the taxable base. Treating that ordinary
    difference as an arithmetic error would block valid bills, so it is only a
    real mismatch when the written figure matches neither reading.
    """
    if draft.gst_mode != "gst" or item.written_total_paise is None:
        return False
    rate = _decimal(item.gst_rate or draft.gst_rate)
    if rate is None or not 0 < rate <= 100:
        return False
    inclusive = _round_paise(Decimal(line_total) * (Decimal(100) + rate) / Decimal(100))
    # One paisa of slack absorbs the rounding the printer applied.
    return abs(item.written_total_paise - inclusive) <= 1


def validate_bill(draft: BillDraftData) -> BillCalculation:
    """Calculate a draft and return every missing field/discrepancy."""
    calc = BillCalculation(
        discount_paise=draft.discount_paise,
        extra_charge_paise=draft.extra_charge_paise,
        round_off_paise=draft.round_off_paise,
    )

    if draft.document_kind == "not_bill":
        calc.warnings.append(
            DraftWarning(
                code="not_a_bill",
                field="document_kind",
                severity="error",
                message=(
                    draft.document_reason
                    or "The uploaded image does not appear to be a bill."
                ),
            )
        )
        return calc
    elif draft.document_kind == "uncertain":
        calc.warnings.append(
            DraftWarning(
                code="uncertain_document",
                field="document_kind",
                severity="error",
                message="The image is too unclear to confirm that it is a bill.",
            )
        )
        return calc

    for field, score in draft.confidence.items():
        if score < _LOW_CONFIDENCE:
            calc.warnings.append(
                DraftWarning(
                    code="low_confidence",
                    field=field,
                    message=f"Please verify {field}; the handwriting was unclear.",
                )
            )

    if draft.bill_type is None:
        calc.missing_fields.append("bill_type")
    if not (draft.bill_date or "").strip():
        calc.missing_fields.append("bill_date")
    if not (draft.party.name or "").strip():
        calc.missing_fields.append("party.name")
    if draft.gst_mode is None:
        calc.missing_fields.append("gst_mode")
    if draft.payment_status is None:
        calc.missing_fields.append("payment_status")
    if not draft.items:
        calc.missing_fields.append("items")

    subtotal = 0
    missing_quantities: list[int] = []
    missing_prices: list[int] = []
    for index, item in enumerate(draft.items):
        field = f"items.{index}"
        for item_field, score in item.confidence.items():
            if score < _LOW_CONFIDENCE:
                calc.warnings.append(
                    DraftWarning(
                        code="low_confidence",
                        field=f"{field}.{item_field}",
                        message=(
                            f"Please verify item {index + 1} {item_field}; "
                            "the handwriting was unclear."
                        ),
                    )
                )
        if not (item.name or "").strip():
            calc.missing_fields.append(f"{field}.name")

        quantity = _decimal(item.quantity)
        if quantity is None or quantity <= 0:
            missing_quantities.append(index)
        if item.unit_price_paise is None:
            missing_prices.append(index)

        line_total: int | None = None
        if quantity is not None and quantity > 0 and item.unit_price_paise is not None:
            line_total = _round_paise(quantity * Decimal(item.unit_price_paise))
            subtotal += line_total
            if (
                item.written_total_paise is not None
                and item.written_total_paise != line_total
                and not _matches_tax_inclusive(draft, item, line_total)
            ):
                calc.warnings.append(
                    DraftWarning(
                        code="line_total_mismatch",
                        field=f"{field}.written_total_paise",
                        severity="error",
                        message=(
                            f"Line {index + 1} is written as "
                            f"₹{item.written_total_paise / 100:.2f}, but quantity × "
                            f"price is ₹{line_total / 100:.2f}."
                        ),
                    )
                )
        elif item.written_total_paise is not None:
            # The amount column is still a valid source for the live draft
            # total even when a handwritten quantity/rate needs confirmation.
            # It never makes the bill confirmable: the missing input remains
            # listed above and must be resolved before accounting is updated.
            line_total = item.written_total_paise
            subtotal += line_total

        calc.lines.append(
            LineCalculation(
                index=index,
                name=item.name,
                quantity=item.quantity,
                unit_price_paise=item.unit_price_paise,
                calculated_total_paise=line_total,
            )
        )

    if len(missing_quantities) == 1:
        calc.missing_fields.append(f"items.{missing_quantities[0]}.quantity")
    elif missing_quantities:
        calc.missing_fields.append("items.quantities")
    if len(missing_prices) == 1:
        calc.missing_fields.append(f"items.{missing_prices[0]}.unit_price_paise")
    elif missing_prices:
        calc.missing_fields.append("items.prices")

    calc.subtotal_paise = subtotal
    if (
        draft.written_subtotal_paise is not None
        and draft.written_subtotal_paise != subtotal
    ):
        calc.warnings.append(
            DraftWarning(
                code="subtotal_mismatch",
                field="written_subtotal_paise",
                severity="error",
                message=(
                    f"Written subtotal is ₹{draft.written_subtotal_paise / 100:.2f}, "
                    f"but the item sum is ₹{subtotal / 100:.2f}."
                ),
            )
        )

    taxable = subtotal - draft.discount_paise + draft.extra_charge_paise
    if taxable < 0:
        calc.warnings.append(
            DraftWarning(
                code="discount_exceeds_subtotal",
                field="discount_paise",
                severity="error",
                message="Discount cannot exceed the subtotal plus additional charges.",
            )
        )
        taxable = 0
    calc.taxable_paise = taxable

    if draft.gst_mode == "gst":
        item_rates = {
            (item.gst_rate or "").strip()
            for item in draft.items
            if (item.gst_rate or "").strip()
        }
        if len(item_rates) > 1:
            # One rate cannot represent a bill whose rows are taxed differently,
            # and silently applying one would post a wrong total.
            calc.warnings.append(
                DraftWarning(
                    code="mixed_gst_rates",
                    field="gst_rate",
                    severity="error",
                    message=(
                        "This bill has more than one GST rate ("
                        + ", ".join(f"{rate}%" for rate in sorted(item_rates))
                        + "). Split it into one bill per rate, or set the "
                        "single rate that applies."
                    ),
                )
            )
        rate = _decimal(draft.gst_rate)
        if rate is None or rate < 0:
            calc.missing_fields.append("gst_rate")
        elif rate > 100:
            calc.warnings.append(
                DraftWarning(
                    code="invalid_gst_rate",
                    field="gst_rate",
                    severity="error",
                    message="GST rate must be between 0 and 100 percent.",
                )
            )
        else:
            calc.gst_paise = _round_paise(Decimal(taxable) * rate / Decimal(100))
        if draft.tax_scheme is None:
            calc.missing_fields.append("tax_scheme")
        elif draft.tax_scheme == "cgst_sgst":
            calc.cgst_paise = calc.gst_paise // 2
            calc.sgst_paise = calc.gst_paise - calc.cgst_paise
        elif draft.tax_scheme == "igst":
            calc.igst_paise = calc.gst_paise

    calc.grand_total_paise = (
        calc.taxable_paise + calc.gst_paise + draft.round_off_paise
    )
    if calc.grand_total_paise < 0:
        calc.warnings.append(
            DraftWarning(
                code="negative_grand_total",
                field="round_off_paise",
                severity="error",
                message="The grand total cannot be negative.",
            )
        )

    gst_is_undecided = draft.gst_mode == "gst" and (
        "gst_rate" in calc.missing_fields
        or any(warning.code == "mixed_gst_rates" for warning in calc.warnings)
    )
    if (
        draft.written_grand_total_paise is not None
        and draft.written_grand_total_paise != calc.grand_total_paise
        # Disputing the printed total before the GST rate is settled only
        # produces a second, confusing error about the same missing input.
        and not gst_is_undecided
    ):
        calc.warnings.append(
            DraftWarning(
                code="grand_total_mismatch",
                field="written_grand_total_paise",
                severity="error",
                message=(
                    f"Written grand total is "
                    f"₹{draft.written_grand_total_paise / 100:.2f}, but the "
                    f"verified total is ₹{calc.grand_total_paise / 100:.2f}."
                ),
            )
        )

    if draft.payment_status == "paid":
        calc.paid_paise = calc.grand_total_paise
    elif draft.payment_status == "credit":
        calc.paid_paise = 0
    elif draft.payment_status == "partial":
        if draft.paid_amount_paise is None:
            calc.missing_fields.append("paid_amount_paise")
        else:
            calc.paid_paise = draft.paid_amount_paise
            if not 0 < calc.paid_paise < calc.grand_total_paise:
                calc.warnings.append(
                    DraftWarning(
                        code="invalid_partial_payment",
                        field="paid_amount_paise",
                        severity="error",
                        message=(
                            "A partial payment must be greater than zero and less "
                            "than the grand total."
                        ),
                    )
                )
    calc.due_paise = max(0, calc.grand_total_paise - calc.paid_paise)

    # Keep paths stable and avoid making the UI ask the same question twice.
    calc.missing_fields = list(dict.fromkeys(calc.missing_fields))
    return calc
