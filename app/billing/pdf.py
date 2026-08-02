"""Generate the DukanBook-branded invoice shared by the shopkeeper."""
from __future__ import annotations

import math
import os
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from io import BytesIO

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Circle, Drawing, Group, Line, String
from reportlab.lib import colors
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BRAND = colors.HexColor("#11C5C5")
BRAND_DARK = colors.HexColor("#07383B")
INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#667777")
RULE = colors.HexColor("#B8C3C3")
PAGE_WIDTH, PAGE_HEIGHT = A4
CONTENT_WIDTH = 174 * mm


def _money(paise: int | None) -> str:
    value = int(paise or 0)
    sign = "-" if value < 0 else ""
    return f"{sign}Rs {abs(value) / 100:,.2f}"


_ONES = (
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
)
_TENS = (
    "", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy",
    "Eighty", "Ninety",
)


def _two_digit_words(value: int) -> str:
    if value < 20:
        return _ONES[value]
    return f"{_TENS[value // 10]} {_ONES[value % 10]}".strip()


def _indian_words(value: int) -> str:
    """Spell a whole rupee amount using crore/lakh/thousand grouping."""
    if value == 0:
        return "Zero"
    parts: list[str] = []
    for divisor, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if value >= divisor:
            parts.append(f"{_indian_words(value // divisor)} {label}")
            value %= divisor
    if value >= 100:
        parts.append(f"{_ONES[value // 100]} Hundred")
        value %= 100
    if value:
        parts.append(_two_digit_words(value))
    return " ".join(parts)


def amount_in_words(paise: int | None) -> str:
    """Indian-format amount in words, as expected on a printed invoice."""
    total = abs(int(paise or 0))
    rupees, remainder = divmod(total, 100)
    words = f"{_indian_words(rupees)} Rupees"
    if remainder:
        words += f" and {_two_digit_words(remainder)} Paise"
    return f"{words} Only"


def _rate_text(raw: object) -> str:
    try:
        rate = Decimal(str(raw or "0"))
    except (InvalidOperation, ValueError):
        return "0"
    return format(rate.normalize(), "f")


def build_totals_rows(bill: dict) -> list[dict]:
    """The invoice money ladder.

    Every `ladder` row sums exactly to the `grand` row, so a shopkeeper can add
    up the printed column by hand and land on the amount due. `info` rows are
    explanatory and deliberately excluded from that sum.
    """
    rows: list[dict] = [
        {"label": "Net total", "paise": int(bill["subtotal_paise"]), "kind": "ladder"}
    ]
    discount = int(bill.get("discount_paise") or 0)
    extra = int(bill.get("extra_charge_paise") or 0)
    if discount:
        rows.append({"label": "Discount", "paise": -discount, "kind": "ladder"})
    if extra:
        rows.append({"label": "Extra charges", "paise": extra, "kind": "ladder"})
    if discount or extra:
        rows.append(
            {
                "label": "Taxable value",
                "paise": int(bill["taxable_paise"]),
                "kind": "info",
            }
        )

    if bill["gst_mode"] == "gst":
        rate = _rate_text(bill.get("gst_rate"))
        half = _rate_text(
            (Decimal(rate) / 2) if rate not in ("", "0") else Decimal("0")
        )
        cgst = int(bill.get("cgst_paise") or 0)
        sgst = int(bill.get("sgst_paise") or 0)
        igst = int(bill.get("igst_paise") or 0)
        if igst:
            rows.append({"label": f"IGST @ {rate}%", "paise": igst, "kind": "ladder"})
        else:
            rows.append({"label": f"CGST @ {half}%", "paise": cgst, "kind": "ladder"})
            rows.append({"label": f"SGST @ {half}%", "paise": sgst, "kind": "ladder"})

    round_off = int(bill.get("round_off_paise") or 0)
    if round_off:
        rows.append({"label": "Round off", "paise": round_off, "kind": "ladder"})

    grand = int(bill["grand_total_paise"])
    rows.append({"label": "Total amount due", "paise": grand, "kind": "grand"})

    paid = int(bill.get("paid_paise") or 0)
    if paid:
        rows.append({"label": "Paid", "paise": paid, "kind": "settlement"})
    if grand - paid > 0:
        rows.append(
            {"label": "Balance due", "paise": grand - paid, "kind": "settlement"}
        )
    return rows


def _display_date(raw: str | None) -> str:
    try:
        return date.fromisoformat(str(raw)).strftime("%d/%m/%Y")
    except ValueError:
        return str(raw or "")


def _text(value: object | None) -> str:
    return escape(str(value or "").strip())


def _initials(name: str) -> str:
    words = [word for word in re.split(r"[^\wऀ-ॿ]+", name or "") if word]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:2].title()
    return "".join(word[0] for word in words[:3]).upper()


def _fit_seal_lines(
    label: str, font_size: float, max_width: float, max_lines: int = 2
) -> list[str] | None:
    """Wrap a seal name to at most `max_lines`, or report that it will not fit."""
    words, lines, current = label.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if stringWidth(candidate, "Helvetica-Bold", font_size) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        if stringWidth(word, "Helvetica-Bold", font_size) > max_width:
            return None
        current = word
    if current:
        lines.append(current)
    return lines if lines and len(lines) <= max_lines else None


def build_party_seal(
    name: str, phone: str | None = None, size: float = 30 * mm
) -> Drawing:
    """Round rubber-stamp seal drawn from the party's own name and number.

    Generated per bill rather than shipped as a fixed image, so every customer
    and supplier gets their own stamp.
    """
    drawing = Drawing(size, size)
    centre = size / 2
    outer = centre - 0.6
    inner = outer - size * 0.10
    drawing.add(
        Circle(centre, centre, outer, strokeColor=INK, strokeWidth=0.9, fillColor=None)
    )
    drawing.add(
        Circle(centre, centre, inner, strokeColor=INK, strokeWidth=0.7, fillColor=None)
    )

    digits = re.sub(r"\D", "", str(phone or ""))[-10:]
    if digits:
        radius = (outer + inner) / 2
        # Spread the number across the top arc, letter by letter, so each glyph
        # sits tangent to the ring exactly like a real rubber stamp.
        spread = min(200.0, 13.0 * len(digits))
        start = 90 + spread / 2
        step = spread / max(len(digits) - 1, 1)
        for index, character in enumerate(digits):
            angle = start - index * step
            radians = math.radians(angle)
            glyph = Group(
                String(
                    0,
                    0,
                    character,
                    fontName="Helvetica",
                    fontSize=size * 0.075,
                    fillColor=INK,
                    textAnchor="middle",
                )
            )
            glyph.translate(
                centre + radius * math.cos(radians),
                centre + radius * math.sin(radians) - size * 0.026,
            )
            glyph.rotate(angle - 90)
            drawing.add(glyph)

    band = size * 0.11
    for offset in (band, -band):
        drawing.add(
            Line(
                centre - outer * 0.97,
                centre + offset,
                centre + outer * 0.97,
                centre + offset,
                strokeColor=INK,
                strokeWidth=0.9,
            )
        )

    # The name must stay inside the ring, so measure the actual chord available
    # between the two rules rather than guessing from the radius.
    max_width = 2 * math.sqrt(max(inner**2 - band**2, 1.0)) * 0.92
    label = (name or "").strip().upper()
    font_size = size * 0.13
    lines = _fit_seal_lines(label, font_size, max_width)
    while lines is None and font_size > size * 0.05:
        font_size -= 0.3
        lines = _fit_seal_lines(label, font_size, max_width)
    lines = lines or [label]

    leading = font_size * 1.1
    top = centre + (len(lines) - 1) * leading / 2 - font_size * 0.34
    for index, line in enumerate(lines):
        drawing.add(
            String(
                centre,
                top - index * leading,
                line,
                fontName="Helvetica-Bold",
                fontSize=font_size,
                fillColor=INK,
                textAnchor="middle",
            )
        )

    mark = _initials(name)
    if mark:
        drawing.add(
            String(
                centre,
                centre - outer * 0.62,
                mark,
                fontName="Helvetica",
                fontSize=size * 0.062,
                fillColor=INK,
                textAnchor="middle",
            )
        )
    return drawing


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
        bottomMargin=20 * mm,
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

    # Column set follows the DukanBook invoice template: Description, Date,
    # Quantity, Unit Price, GST, Total. HSN is added only when the bill
    # actually carries one, so the common bill keeps the template's look.
    is_gst = bill["gst_mode"] == "gst"
    show_hsn = any((item.get("hsn") or "").strip() for item in bill["items"])
    header_cells = [Paragraph("<b>Description</b>", cell)]
    if show_hsn:
        header_cells.append(Paragraph("<b>HSN</b>", cell))
    header_cells += [
        Paragraph("<b>Date</b>", cell),
        Paragraph("<b>Quantity</b>", cell_right),
        Paragraph("<b>Unit Price</b>", cell_right),
    ]
    if is_gst:
        header_cells.append(Paragraph("<b>GST</b>", cell_right))
    header_cells.append(Paragraph("<b>Total</b>", cell_right))
    item_rows = [header_cells]

    global_gst = _rate_text(bill.get("gst_rate")) if is_gst else "0"
    for item in bill["items"]:
        quantity = f"{item['quantity']} {item.get('unit') or ''}".strip()
        row = [Paragraph(_text(item["name"]), cell)]
        if show_hsn:
            row.append(Paragraph(_text(item.get("hsn")) or "-", cell))
        row += [
            Paragraph(_display_date(bill["bill_date"]), cell),
            Paragraph(_text(quantity), cell_right),
            Paragraph(_money(item["unit_price_paise"]), cell_right),
        ]
        if is_gst:
            row.append(
                Paragraph(
                    f"{_rate_text(item.get('gst_rate') or global_gst)} %", cell_right
                )
            )
        # Deliberate correction to the template: the Total column is the line's
        # taxable amount, so the column sums exactly to Net total. Printing it
        # tax-inclusive here declares GST twice and stops reconciling as soon
        # as the bill has several items or a discount.
        row.append(Paragraph(_money(item["line_total_paise"]), cell_right))
        item_rows.append(row)

    flexible = CONTENT_WIDTH - (
        (18 * mm if show_hsn else 0)
        + 24 * mm
        + 22 * mm
        + 28 * mm
        + (16 * mm if is_gst else 0)
        + 32 * mm
    )
    column_widths = [flexible]
    if show_hsn:
        column_widths.append(18 * mm)
    column_widths += [24 * mm, 22 * mm, 28 * mm]
    if is_gst:
        column_widths.append(16 * mm)
    column_widths.append(32 * mm)

    items = Table(
        item_rows,
        colWidths=column_widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    first_right_column = 2 + (1 if show_hsn else 0)
    items.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (first_right_column, 0), (-1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.45, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    grand_label = ParagraphStyle(
        "DueLabel", parent=total_label, fontSize=11, leading=13
    )
    grand_value = ParagraphStyle(
        "DueValue", parent=total_value, fontSize=11, leading=13
    )
    info_label = ParagraphStyle("InfoLabel", parent=total_label, textColor=MUTED)
    info_value = ParagraphStyle("InfoValue", parent=total_value, textColor=MUTED)

    rows = build_totals_rows(bill)
    totals_data = []
    grand_index = 0
    for index, row in enumerate(rows):
        if row["kind"] == "grand":
            grand_index = index
            label_style, value_style = grand_label, grand_value
        elif row["kind"] == "info":
            label_style, value_style = info_label, info_value
        else:
            label_style, value_style = total_label, total_value
        totals_data.append(
            [
                Paragraph(row["label"], label_style),
                Paragraph(_money(row["paise"]), value_style),
            ]
        )
    totals = Table(totals_data, colWidths=[38 * mm, 38 * mm], hAlign="RIGHT")
    totals.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("LINEABOVE", (0, grand_index), (-1, grand_index), 1, INK),
                ("LINEBELOW", (0, grand_index), (-1, grand_index), 1, RULE),
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

    sign_right = ParagraphStyle("SignBlock", parent=small, alignment=TA_RIGHT)
    seal = build_party_seal(bill["party_name"], bill.get("party_phone"), 25 * mm)
    signature = Table(
        [
            [
                Paragraph("Received the above goods in good condition.", small),
                Paragraph(f"For <b>{_text(issuer_name)}</b>", sign_right),
            ],
            ["", seal],
            ["", Paragraph("Authorised Signatory", sign_right)],
        ],
        colWidths=[CONTENT_WIDTH - 62 * mm, 62 * mm],
    )
    signature.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                # The rule belongs directly above the signatory caption, not
                # above the firm name.
                ("LINEBELOW", (1, 1), (1, 1), 0.5, INK),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    story = [
        header,
        Spacer(1, 10 * mm),
        meta,
        Spacer(1, 12 * mm),
        Paragraph("INVOICE", title_style),
        Spacer(1, 7 * mm),
        items,
        Spacer(1, 4 * mm),
        totals,
        Spacer(1, 4 * mm),
        Paragraph(
            f"<b>Amount in words:</b> {_text(amount_in_words(bill['grand_total_paise']))}",
            small,
        ),
        Spacer(1, 2 * mm),
        Paragraph(f"<b>Payment status:</b> {_text(payment)}", small),
    ]
    if bill.get("note"):
        story += [
            Spacer(1, 2 * mm),
            Paragraph(f"<b>Note:</b> {_text(bill['note'])}", small),
        ]
    # Keep the closing block whole: a stamp stranded on its own page reads as a
    # printing fault rather than a signed invoice.
    story += [Spacer(1, 6 * mm), KeepTogether(signature)]
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return stream.getvalue()
