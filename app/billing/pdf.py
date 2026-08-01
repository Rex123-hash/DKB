"""Generate the DukanBook-branded invoice shared by the shopkeeper."""
from __future__ import annotations

import os
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from io import BytesIO

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BRAND = colors.HexColor("#11C5C5")
BRAND_DARK = colors.HexColor("#07383B")
INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#667777")
RULE = colors.HexColor("#B8C3C3")
PAGE_WIDTH, PAGE_HEIGHT = A4
CONTENT_WIDTH = 174 * mm


def _money(paise: int | None) -> str:
    return f"Rs {int(paise or 0) / 100:,.2f}"


def _line_total_with_gst(line_total_paise: int, gst_rate: object) -> int:
    try:
        rate = Decimal(str(gst_rate or "0"))
    except (InvalidOperation, ValueError):
        rate = Decimal("0")
    gross = Decimal(line_total_paise) * (Decimal("1") + rate / Decimal("100"))
    return int(gross.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _display_date(raw: str | None) -> str:
    try:
        return date.fromisoformat(str(raw)).strftime("%d/%m/%Y")
    except ValueError:
        return str(raw or "")


def _text(value: object | None) -> str:
    return escape(str(value or "").strip())


def _qr(value: str, size: float = 20 * mm) -> Drawing:
    widget = QrCodeWidget(value)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(
        size,
        size,
        transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0],
    )
    drawing.add(widget)
    return drawing


def _footer(canvas, document) -> None:
    canvas.saveState()
    left = 18 * mm
    right = PAGE_WIDTH - 18 * mm
    canvas.setStrokeColor(INK)
    canvas.setLineWidth(0.7)
    canvas.line(left, 16 * mm, right, 16 * mm)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawCentredString(PAGE_WIDTH / 2, 9.5 * mm, "Powered by DukanBook")
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(right, 9.5 * mm, f"Page {document.page}")
    canvas.restoreState()


def build_bill_pdf(bill: dict) -> bytes:
    """Render a finalized sale/purchase as a professional A4 invoice."""
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=14 * mm,
        bottomMargin=24 * mm,
        title=f"DukanBook Invoice {bill['bill_number']}",
        author="DukanBook AI Assistant",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "InvoiceBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        textColor=INK,
        fontSize=9.2,
        leading=12,
    )
    small = ParagraphStyle(
        "InvoiceSmall", parent=body, fontSize=7.8, leading=10
    )
    meta_label = ParagraphStyle(
        "InvoiceMetaLabel", parent=body, fontName="Helvetica-Bold", fontSize=8.7
    )
    meta_value = ParagraphStyle(
        "InvoiceMetaValue", parent=body, alignment=TA_RIGHT, fontSize=8.7
    )
    title_style = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        textColor=INK,
        fontSize=24,
        leading=28,
        alignment=TA_LEFT,
    )
    cell = ParagraphStyle("InvoiceCell", parent=small, fontSize=7.6, leading=9)
    cell_right = ParagraphStyle(
        "InvoiceCellRight", parent=cell, alignment=TA_RIGHT
    )
    total_label = ParagraphStyle(
        "InvoiceTotalLabel", parent=body, fontName="Helvetica-Bold", fontSize=8.8
    )
    total_value = ParagraphStyle(
        "InvoiceTotalValue",
        parent=total_label,
        alignment=TA_RIGHT,
    )

    business_name = os.environ.get("DUKANBOOK_BUSINESS_NAME", "DukanBook")
    business_phone = os.environ.get("DUKANBOOK_BUSINESS_PHONE", "")
    business_gstin = os.environ.get("DUKANBOOK_BUSINESS_GSTIN", "")
    is_purchase = bill["type"] == "purchase"

    if is_purchase:
        issuer_name = bill["party_name"]
        issuer_phone = bill.get("party_phone") or ""
        issuer_gstin = bill.get("gstin") or ""
        recipient_name = business_name
        recipient_phone = business_phone
        recipient_gstin = business_gstin
    else:
        issuer_name = business_name
        issuer_phone = business_phone
        issuer_gstin = business_gstin
        recipient_name = bill["party_name"]
        recipient_phone = bill.get("party_phone") or ""
        recipient_gstin = bill.get("gstin") or ""

    issuer_lines = [
        f"<b>{_text(issuer_name)}</b>",
        _text(bill["type"].title()),
    ]
    if issuer_phone:
        issuer_lines.append(_text(issuer_phone))
    if issuer_gstin:
        issuer_lines.append(f"GSTIN: {_text(issuer_gstin)}")

    qr_value = (
        f"DukanBook invoice {bill['bill_number']} | {bill['bill_date']} | "
        f"{_money(bill['grand_total_paise'])}"
    )
    header = Table(
        [[Paragraph("<br/>".join(issuer_lines), body), _qr(qr_value)]],
        colWidths=[CONTENT_WIDTH - 28 * mm, 28 * mm],
        rowHeights=[28 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BRAND),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "CENTER"),
                ("LEFTPADDING", (0, 0), (0, 0), 8),
                ("RIGHTPADDING", (1, 0), (1, 0), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    recipient_lines = [f"<b>{_text(recipient_name)}</b>"]
    if recipient_phone:
        recipient_lines.append(_text(recipient_phone))
    if recipient_gstin:
        recipient_lines.append(f"GSTIN: {_text(recipient_gstin)}")

    meta = Table(
        [
            [
                Paragraph("<b>TO</b><br/>" + "<br/>".join(recipient_lines), body),
                Paragraph("Invoice Number:", meta_label),
                Paragraph(_text(bill["bill_number"]), meta_value),
            ],
            [
                "",
                Paragraph("Invoice Date:", meta_label),
                Paragraph(_display_date(bill["bill_date"]), meta_value),
            ],
        ],
        colWidths=[108 * mm, 34 * mm, 32 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 0), (0, 1)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    item_rows = [
        [
            Paragraph("<b>Description</b>", cell),
            Paragraph("<b>Date</b>", cell),
            Paragraph("<b>Quantity</b>", cell_right),
            Paragraph("<b>Unit Price</b>", cell_right),
            Paragraph("<b>GST</b>", cell_right),
            Paragraph("<b>Total</b>", cell_right),
        ]
    ]
    global_gst = f"{bill.get('gst_rate') or '0'}%" if bill["gst_mode"] == "gst" else "0%"
    for item in bill["items"]:
        quantity = f"{item['quantity']} {item.get('unit') or ''}".strip()
        item_gst = str(item.get("gst_rate") or global_gst).strip()
        if not item_gst.endswith("%"):
            item_gst += "%"
        gross_total = int(item["line_total_paise"])
        if bill["gst_mode"] == "gst":
            gross_total = _line_total_with_gst(
                gross_total, str(item_gst).rstrip("%").strip()
            )
        item_rows.append(
            [
                Paragraph(_text(item["name"]), cell),
                Paragraph(_display_date(bill["bill_date"]), cell),
                Paragraph(_text(quantity), cell_right),
                Paragraph(_money(item["unit_price_paise"]), cell_right),
                Paragraph(_text(item_gst), cell_right),
                Paragraph(_money(gross_total), cell_right),
            ]
        )

    items = Table(
        item_rows,
        colWidths=[49 * mm, 24 * mm, 21 * mm, 29 * mm, 17 * mm, 34 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 1), (-1, -1), 0.45, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    gst_paise = int(bill.get("cgst_paise") or 0) + int(
        bill.get("sgst_paise") or 0
    ) + int(bill.get("igst_paise") or 0)
    totals_data = [
        [Paragraph("Net total", total_label), Paragraph(_money(bill["taxable_paise"]), total_value)]
    ]
    if bill["discount_paise"]:
        totals_data.append(
            [Paragraph("Discount", total_label), Paragraph(f"- {_money(bill['discount_paise'])}", total_value)]
        )
    if bill["extra_charge_paise"]:
        totals_data.append(
            [Paragraph("Extra charges", total_label), Paragraph(_money(bill["extra_charge_paise"]), total_value)]
        )
    if bill["gst_mode"] == "gst":
        totals_data.append(
            [Paragraph(f"GST {_text(bill.get('gst_rate') or '0')}%", total_label), Paragraph(_money(gst_paise), total_value)]
        )
    totals_data.append(
        [Paragraph("Total amount<br/>due", ParagraphStyle("DueLabel", parent=total_label, fontSize=11, leading=13)), Paragraph(_money(bill["grand_total_paise"]), ParagraphStyle("DueValue", parent=total_value, fontSize=11, leading=13))]
    )
    totals = Table(totals_data, colWidths=[35 * mm, 37 * mm], hAlign="RIGHT")
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, -1), (-1, -1), 1, INK),
                ("LINEBELOW", (0, -1), (-1, -1), 1, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )

    payment = bill["payment_status"].title()
    if bill["payment_status"] == "partial":
        payment += f" - paid {_money(bill['paid_paise'])}"
    story = [
        header,
        Spacer(1, 10 * mm),
        meta,
        Spacer(1, 17 * mm),
        Paragraph("INVOICE", title_style),
        Spacer(1, 9 * mm),
        items,
        Spacer(1, 4 * mm),
        totals,
        Spacer(1, 5 * mm),
        Paragraph(f"Payment status: <b>{_text(payment)}</b>", small),
    ]
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return stream.getvalue()
