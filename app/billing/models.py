"""Canonical models shared by every bill-extraction provider."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


BillType = Literal["sale", "purchase"]
DocumentKind = Literal["bill", "not_bill", "uncertain"]
GSTMode = Literal["gst", "non_gst"]
TaxScheme = Literal["cgst_sgst", "igst"]
PaymentStatus = Literal["paid", "credit", "partial"]


class PartyDraft(BaseModel):
    name: str | None = Field(
        default=None,
        description=(
            "Visible customer/supplier name; when only a supplier letterhead "
            "is present, use its prominent printed business name."
        ),
    )
    phone: str | None = None
    gstin: str | None = None


class BillItemDraft(BaseModel):
    name: str | None = Field(
        default=None, description="Best visible item/product description."
    )
    quantity: str | None = Field(
        default=None,
        description=(
            "Value from the Qty column only; do not use row serial numbers."
        ),
    )
    unit: str | None = None
    unit_price_paise: int | None = Field(
        default=None,
        ge=0,
        description="Visible per-unit rate converted to integer paise.",
    )
    written_total_paise: int | None = Field(
        default=None,
        ge=0,
        description="Visible line amount converted to integer paise.",
    )
    hsn: str | None = None
    gst_rate: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)


class BillDraftData(BaseModel):
    extractor_version: str | None = None
    document_kind: DocumentKind | None = None
    document_reason: str | None = None
    bill_type: BillType | None = None
    bill_number: str | None = Field(
        default=None, description="Visible invoice, receipt, or memo number."
    )
    bill_date: str | None = Field(
        default=None,
        description=(
            "Fully readable bill date normalized to YYYY-MM-DD. Return null "
            "only when a complete date is not visible."
        ),
    )
    party: PartyDraft = Field(default_factory=PartyDraft)
    gst_mode: GSTMode | None = None
    tax_scheme: TaxScheme | None = None
    gst_rate: str | None = None
    items: list[BillItemDraft] = Field(default_factory=list)
    discount_paise: int = Field(default=0, ge=0)
    extra_charge_paise: int = Field(default=0, ge=0)
    round_off_paise: int = 0
    written_subtotal_paise: int | None = Field(
        default=None, ge=0, description="Visible written subtotal in paise."
    )
    written_grand_total_paise: int | None = Field(
        default=None, ge=0, description="Visible final payable total in paise."
    )
    payment_status: PaymentStatus | None = None
    paid_amount_paise: int | None = Field(default=None, ge=0)
    note: str | None = None
    confidence: dict[str, float] = Field(default_factory=dict)


class DraftWarning(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: Literal["warning", "error"] = "warning"


class LineCalculation(BaseModel):
    index: int
    name: str | None
    quantity: str | None
    unit_price_paise: int | None
    calculated_total_paise: int | None


class BillCalculation(BaseModel):
    lines: list[LineCalculation] = Field(default_factory=list)
    subtotal_paise: int = 0
    discount_paise: int = 0
    extra_charge_paise: int = 0
    taxable_paise: int = 0
    gst_paise: int = 0
    cgst_paise: int = 0
    sgst_paise: int = 0
    igst_paise: int = 0
    round_off_paise: int = 0
    grand_total_paise: int = 0
    paid_paise: int = 0
    due_paise: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[DraftWarning] = Field(default_factory=list)

    @property
    def is_ready(self) -> bool:
        return not self.missing_fields and not any(
            warning.severity == "error" for warning in self.warnings
        )
