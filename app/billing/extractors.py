"""Bill image extraction providers.

`fake`, `ollama`, and the future `vertex` provider all return BillDraftData.
No provider is allowed to perform accounting calculations or database writes.
"""
from __future__ import annotations

import base64
import json
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol

import requests
from pydantic import BaseModel, Field

from app import config
from app.billing.models import BillDraftData, BillItemDraft


class BillExtractor(Protocol):
    name: str
    version: str

    def extract(
        self, image_bytes: bytes, mime_type: str, filename: str
    ) -> BillDraftData: ...

    def refine(self, draft: BillDraftData, answer: str) -> BillDraftData: ...


EXTRACTION_PROMPT = """
You extract handwritten Indian shop bills into JSON.

Rules:
- First classify document_kind as bill, not_bill, or uncertain.
- A handwritten/printed invoice, receipt, sale bill, or purchase bill is a bill.
- A stock summary, inventory report, product list, dashboard, unrelated app
  screenshot, or other non-transaction document is not_bill.
- If document_kind is not_bill, explain why in document_reason and leave all
  bill fields null with an empty items array.
- If the image is too unclear to classify, use uncertain and do not guess.
- Copy only information visible in the image. Unknown or unreadable values are null.
- Never calculate or repair a number.
- bill_type is sale or purchase only when the bill clearly says so; otherwise null.
- All money values are integer paise: Rs 12.50 = 1250 and Rs 12 = 1200.
- quantity and gst_rate are decimal strings.
- gst_mode is gst or non_gst only when visible; otherwise null.
- tax_scheme is cgst_sgst or igst only when visible; otherwise null.
- payment_status is paid, credit, or partial only when visible.
- Keep Hindi/Hinglish names in the script visible on the bill.
- Put extraction confidence from 0 to 1 in the confidence maps.
- Return only JSON that matches the supplied schema.
""".strip()


class BillHeaderRead(BaseModel):
    """Small required schema makes local models read rather than omit headers."""

    business_name: str
    bill_number: str
    date_text: str
    bill_date: str


class BillItemRead(BaseModel):
    """Line-item table read on its own.

    A single large schema makes the model return a partial object and drop the
    items array entirely, even on a crisp printed invoice. A small schema whose
    fields are all required makes it actually read the table. Amounts are read
    as printed rupee strings, never as paise, so the model never has to perform
    the one conversion it is forbidden to calculate.
    """

    name: str
    quantity: str
    unit: str
    unit_price_rupees: str
    amount_rupees: str
    hsn: str
    gst_rate: str


class BillItemsRead(BaseModel):
    items: list[BillItemRead] = Field(min_length=1)


ITEMS_PROMPT = """
Read ONLY the line-item table of this Indian bill.
Return one object per real product row, top to bottom, in the printed order.
Every schema field is required: use an empty string when a cell is blank or
unreadable. Copy the digits exactly as printed and never calculate, merge,
split, or invent a row.
- Serial numbers (Sr, S.No) are not quantities.
- Total, Subtotal, Taxable, CGST/SGST/IGST, Grand Total, amount-in-words,
  terms and conditions, bank details, and the signature block are NOT rows.
- Read handwritten rows as carefully as printed ones.
""".strip()


def _rupees_to_paise(raw: str | None) -> int | None:
    """Convert a printed rupee amount to integer paise."""
    text = re.sub(r"[^\d.\-]", "", str(raw or ""))
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        value = Decimal(text)
    except Exception:
        return None
    if value < 0:
        return None
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _clean_decimal_text(raw: str | None) -> str | None:
    """Keep a printed quantity/rate as an exact decimal string."""
    text = re.sub(r"[^\d.]", "", str(raw or ""))
    if not text:
        return None
    try:
        value = Decimal(text)
    except Exception:
        return None
    if value <= 0:
        return None
    formatted = format(value, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def recover_items(provider, draft: BillDraftData, **request_kwargs) -> None:
    """Re-read the item table alone when the main pass returned no rows.

    The combined schema is large enough that the model regularly answers with a
    partial object and omits `items` entirely — even for a crisp printed
    invoice. Asking again for only the table reliably recovers it.
    """
    if draft.document_kind != "bill" or draft.items:
        return
    try:
        read = BillItemsRead.model_validate(
            provider._request(
                ITEMS_PROMPT, BillItemsRead.model_json_schema(), **request_kwargs
            )
        )
    except (requests.RequestException, ValueError):
        # Recovery is best effort; the shopkeeper can still add rows in Review.
        return
    draft.items = items_from_read(read)


def items_from_read(read: BillItemsRead) -> list[BillItemDraft]:
    """Turn a focused table read into canonical draft items."""
    items: list[BillItemDraft] = []
    for row in read.items:
        name = (row.name or "").strip()
        unit_price = _rupees_to_paise(row.unit_price_rupees)
        amount = _rupees_to_paise(row.amount_rupees)
        if not name and unit_price is None and amount is None:
            continue
        items.append(
            BillItemDraft(
                name=name or None,
                quantity=_clean_decimal_text(row.quantity),
                unit=(row.unit or "").strip() or None,
                unit_price_paise=unit_price,
                written_total_paise=amount,
                hsn=(row.hsn or "").strip() or None,
                gst_rate=_clean_decimal_text(row.gst_rate),
            )
        )
    return items


HEADER_PROMPT = """
Read only the header of this Indian bill/cash memo.
Return every schema field. Use an empty string when text is absent or unreadable;
never invent it. business_name is the prominent printed company/shop name.
Copy date_text exactly. Set bill_date to YYYY-MM-DD only when the complete date
is safely readable, otherwise use an empty string.
""".strip()


GEMINI_EXTRACTION_PROMPT = f"""
{EXTRACTION_PROMPT}

Read the image top-to-bottom before returning JSON. Specifically inspect the
printed letterhead, bill number, the date beside Dated/Date, every real item
row, Qty/Rate/Amount columns, and the handwritten totals at the bottom.
- Convert Indian dates such as 7/25/2026 or 25/7/2026 to YYYY-MM-DD when valid.
- Do not return placeholder/empty item objects.
- Row serial numbers inside Particulars are not quantities.
- When only a row amount is visible, put it in written_total_paise and leave
  unit_price_paise null; deterministic code will derive a rate when possible.
- Totals, signatures, stamps, E.&O.E. and footer text are never item rows.
""".strip()


def remove_empty_items(draft: BillDraftData) -> BillDraftData:
    """Normalize provider output before it is exposed as a bill draft."""
    cleaned = draft.model_copy(deep=True)
    if cleaned.document_kind in ("not_bill", "uncertain"):
        # A model can classify correctly and still copy inventory rows into the
        # bill schema. Classification is the gate: non-bill data must not enter
        # review, calculation, or accounting.
        return BillDraftData(
            document_kind=cleaned.document_kind,
            document_reason=cleaned.document_reason,
        )
    cleaned.items = [
        item
        for item in cleaned.items
        if any(
            (
                (item.name or "").strip(),
                (item.quantity or "").strip(),
                (item.unit or "").strip(),
                item.unit_price_paise is not None,
                item.written_total_paise is not None,
            )
        )
    ]
    cleaned.bill_date = normalize_bill_date(cleaned.bill_date)
    cleaned.party.phone = _clean_phone(cleaned.party.phone)
    cleaned.party.gstin = _clean_gstin(cleaned.party.gstin)
    for item in cleaned.items:
        # "5.00" and "5" are the same rate. Without this they compare as two
        # different rates and a single-rate bill is reported as mixed.
        item.gst_rate = _clean_decimal_text(item.gst_rate)
    cleaned.gst_rate = _clean_decimal_text(cleaned.gst_rate) or cleaned.gst_rate
    _settle_gst_from_evidence(cleaned)
    for item in cleaned.items:
        quantity = _positive_decimal(item.quantity)
        if quantity is None:
            item.quantity = None
            continue
        quantity_text = format(quantity, "f")
        if "." in quantity_text:
            quantity_text = quantity_text.rstrip("0").rstrip(".")
        item.quantity = quantity_text
        if item.unit_price_paise is None and item.written_total_paise is not None:
            derived = Decimal(item.written_total_paise) / quantity
            if derived == derived.to_integral_value():
                item.unit_price_paise = int(derived)
    return cleaned


def _settle_gst_from_evidence(draft: BillDraftData) -> None:
    """Read the GST mode and rate off the bill instead of asking for them.

    A printed tax scheme or a Tax % column is visible evidence, not a guess. A
    single rate shared by every row is the bill's rate; mixed rates are left
    unset so the shopkeeper decides rather than having one silently applied.
    """
    rates = {
        (item.gst_rate or "").strip()
        for item in draft.items
        if (item.gst_rate or "").strip()
    }
    taxed = {rate for rate in rates if _positive_decimal(rate) is not None}
    if draft.gst_mode is None and (draft.tax_scheme is not None or taxed):
        draft.gst_mode = "gst"
    if draft.gst_mode == "gst" and draft.gst_rate is None and len(taxed) == 1:
        draft.gst_rate = next(iter(taxed))


def _clean_phone(raw: str | None) -> str | None:
    """Keep a phone only when it is a real 10-digit Indian mobile.

    Vision models sometimes run neighbouring cells together — one real read
    returned "+91 8282828281<junk>GSTIN: 09AAACH...". Garbage is not
    information, and an unusable number would otherwise block finalization.
    """
    # Scan each run of digits separately. Concatenating them would merge the
    # phone with whatever the model ran into it (a GSTIN, a PAN) and destroy
    # a number that was actually read correctly.
    text = re.sub(r"[\s\-()]", "", str(raw or ""))
    for run in re.findall(r"\d+", text):
        if len(run) == 12 and run.startswith("91"):
            run = run[2:]
        elif len(run) == 11 and run.startswith("0"):
            run = run[1:]
        if len(run) == 10 and run[0] in "6789":
            return run
    return None


def _clean_gstin(raw: str | None) -> str | None:
    match = re.search(
        r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d])\b", str(raw or "").upper()
    )
    return match.group(1) if match else None


def _positive_decimal(value: str | None) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def normalize_bill_date(value: str | None) -> str | None:
    """Normalize common Indian numeric bill dates without asking twice."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})", text)
    if not match:
        return None
    first, second, year_text = (int(part) for part in match.groups())
    year = 2000 + year_text if year_text < 100 else year_text
    # Indian documents normally use DD/MM. If the first component cannot be a
    # month, this is unambiguous; for MM/DD only accept it when DD/MM is invalid.
    candidates = [(first, second)]
    if first <= 12 and second > 12:
        candidates = [(second, first)]
    for day_value, month_value in candidates:
        try:
            return date(year, month_value, day_value).isoformat()
        except ValueError:
            continue
    return None

REFINEMENT_PROMPT = """
You update a structured Indian shop-bill draft from a trusted shopkeeper's
text or voice clarification.

Rules:
- Apply only information supplied in the correction.
- Preserve every unrelated field exactly as it appears in the current draft.
- User-provided facts may fill values that were unreadable in the image.
- Never calculate or repair line totals, tax, or grand totals.
- All money values are integer paise: Rs 12.50 = 1250.
- If the correction is ambiguous, preserve the current value or leave it null.
- Return only JSON matching the supplied schema.
""".strip()


def _clean_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("bill extractor returned a non-object JSON value")
    return value


class FakeBillExtractor:
    """Deterministic extractor for automated tests and UI development."""

    name = "fake"
    version = "fake-v1"

    def __init__(self, result: BillDraftData | dict | None = None):
        self._result = (
            BillDraftData.model_validate(result)
            if result is not None
            else None
        )

    def _configured_result(self) -> BillDraftData | None:
        path = os.environ.get("BILL_FAKE_JSON")
        if not path:
            return None
        return BillDraftData.model_validate_json(
            Path(path).read_text(encoding="utf-8")
        )

    def extract(
        self, image_bytes: bytes, mime_type: str, filename: str
    ) -> BillDraftData:
        configured = self._result or self._configured_result()
        if configured is not None:
            return remove_empty_items(configured.model_copy(deep=True))
        # An empty, honest draft exercises the clarification workflow without
        # pretending the fake provider read values from the user's image.
        return BillDraftData(
            document_kind="bill", note=f"Uploaded scan: {filename}"
        )

    def refine(self, draft: BillDraftData, answer: str) -> BillDraftData:
        return apply_simple_answer(draft, answer)


class OllamaBillExtractor:
    name = "ollama"
    version = "ollama-v3-header-and-batched-questions"

    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        timeout: float | None = None,
    ):
        self.model = model or os.environ.get(
            "BILL_OLLAMA_MODEL", "gemma3:4b"
        )
        self.url = url or _ollama_chat_url()
        self.timeout = timeout or float(
            os.environ.get("BILL_OLLAMA_TIMEOUT", "180")
        )
        self.num_ctx = int(os.environ.get("BILL_OLLAMA_NUM_CTX", "8192"))

    def _request(self, messages: list[dict], schema: dict) -> dict:
        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {
                    "temperature": 0,
                    "num_ctx": self.num_ctx,
                },
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload.get("message", {}).get("content", "")
        return _clean_json(content)

    def _chat(self, messages: list[dict]) -> BillDraftData:
        return BillDraftData.model_validate(
            self._request(messages, BillDraftData.model_json_schema())
        )

    def extract(
        self, image_bytes: bytes, mime_type: str, filename: str
    ) -> BillDraftData:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        draft = self._chat(
            [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Extract this bill image ({filename}, {mime_type}) "
                        "into the required schema."
                    ),
                    "images": [encoded],
                },
            ]
        )
        if (
            draft.bill_type is not None
            and draft.confidence.get("bill_type", 0) < 0.8
        ):
            # A generic CASH MEMO does not reveal whether this shopkeeper is
            # entering it as their sale or purchase. Ask instead of guessing.
            draft.bill_type = None
        if draft.document_kind == "bill" and not draft.items:
            try:
                draft.items = items_from_read(
                    BillItemsRead.model_validate(
                        self._request(
                            [
                                {"role": "system", "content": ITEMS_PROMPT},
                                {
                                    "role": "user",
                                    "content": "Read the item table.",
                                    "images": [encoded],
                                },
                            ],
                            BillItemsRead.model_json_schema(),
                        )
                    )
                )
            except (requests.RequestException, ValueError):
                pass
        if draft.document_kind == "bill":
            try:
                header = BillHeaderRead.model_validate(
                    self._request(
                        [
                            {"role": "system", "content": HEADER_PROMPT},
                            {
                                "role": "user",
                                "content": "Read the visible bill header.",
                                "images": [encoded],
                            },
                        ],
                        BillHeaderRead.model_json_schema(),
                    )
                )
                if not (draft.party.name or "").strip() and header.business_name.strip():
                    draft.party.name = header.business_name.strip()
                if not (draft.bill_number or "").strip() and header.bill_number.strip():
                    draft.bill_number = header.bill_number.strip()
                if not (draft.bill_date or "").strip():
                    draft.bill_date = normalize_bill_date(
                        header.bill_date or header.date_text
                    )
            except (requests.RequestException, ValueError):
                # A header retry is enrichment only; keep the useful item pass.
                pass
        return remove_empty_items(draft)

    def refine(self, draft: BillDraftData, answer: str) -> BillDraftData:
        # Resolve the common one-question-at-a-time answers deterministically.
        # This keeps "purchase", "non-GST", dates, and payment states reliable
        # even when a small local model is overly conservative.
        deterministic = apply_simple_answer(draft, answer)
        if deterministic.model_dump() != draft.model_dump():
            return deterministic
        prompt = (
            "Update the bill draft using only the user's correction. Preserve "
            "every unrelated field exactly. If the answer is ambiguous, leave "
            "the affected field null. Return the complete schema.\n\n"
            f"Current draft:\n{draft.model_dump_json()}\n\n"
            f"User correction:\n{answer}"
        )
        return self._chat(
            [
                {"role": "system", "content": REFINEMENT_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )


class GeminiBillExtractor:
    """High-accuracy multimodal extractor using the configured Gemini key."""

    name = "gemini"
    version = "gemini-v1-structured-vision"

    def __init__(self, model: str | None = None, timeout: float | None = None):
        self.model = model or os.environ.get(
            "BILL_GEMINI_MODEL", "gemini-3.6-flash"
        )
        self.timeout = timeout or float(
            os.environ.get("BILL_GEMINI_TIMEOUT", "120")
        )
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini bill vision")

    def _request(
        self,
        prompt: str,
        schema: dict,
        *,
        image_bytes: bytes | None = None,
        mime_type: str | None = None,
    ) -> dict:
        parts: list[dict] = [{"text": prompt}]
        if image_bytes is not None:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": mime_type or "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                }
            )
        response = requests.post(
            (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            ),
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "temperature": 0,
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                },
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        try:
            content = payload["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ValueError("Gemini returned no structured bill result") from exc
        return _clean_json(content)

    def extract(
        self, image_bytes: bytes, mime_type: str, filename: str
    ) -> BillDraftData:
        draft = BillDraftData.model_validate(
            self._request(
                f"{GEMINI_EXTRACTION_PROMPT}\n\nFilename: {filename}",
                BillDraftData.model_json_schema(),
                image_bytes=image_bytes,
                mime_type=mime_type,
            )
        )
        recover_items(self, draft, image_bytes=image_bytes, mime_type=mime_type)
        # The combined pass drops different header fields on each call, so
        # re-read the header whenever any of them is still missing.
        if draft.document_kind == "bill" and not (
            (draft.party.name or "").strip()
            and (draft.bill_number or "").strip()
            and (draft.bill_date or "").strip()
        ):
            try:
                header = BillHeaderRead.model_validate(
                    self._request(
                        HEADER_PROMPT,
                        BillHeaderRead.model_json_schema(),
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                    )
                )
                if header.business_name.strip():
                    draft.party.name = header.business_name.strip()
                if not (draft.bill_number or "").strip() and header.bill_number.strip():
                    draft.bill_number = header.bill_number.strip()
                if not (draft.bill_date or "").strip():
                    draft.bill_date = normalize_bill_date(
                        header.bill_date or header.date_text
                    )
            except (requests.RequestException, ValueError):
                pass
        return remove_empty_items(draft)

    def refine(self, draft: BillDraftData, answer: str) -> BillDraftData:
        deterministic = apply_simple_answer(draft, answer)
        if deterministic.model_dump() != draft.model_dump():
            return deterministic
        prompt = (
            f"{REFINEMENT_PROMPT}\n\nCurrent draft:\n"
            f"{draft.model_dump_json()}\n\nUser correction:\n{answer}"
        )
        return remove_empty_items(
            BillDraftData.model_validate(
                self._request(prompt, BillDraftData.model_json_schema())
            )
        )


class VertexBillExtractor:
    """GCP bill reader: optional Document AI OCR + Gemini vision on Vertex.

    OCR is context only.  The original image remains authoritative and all
    arithmetic stays in DukanBook's deterministic calculation layer.
    """

    name = "vertex"
    version = "vertex-gemini-document-ai-v1"

    def _request(
        self,
        prompt: str,
        schema: dict,
        *,
        image_bytes: bytes | None = None,
        mime_type: str | None = None,
        ocr_text: str = "",
    ) -> dict:
        from app import gcp

        full_prompt = prompt
        if ocr_text:
            full_prompt += (
                "\n\nDocument AI OCR transcript follows. Treat it only as a reading "
                "aid; prefer the image whenever they disagree and never invent a "
                f"missing value.\n<ocr>\n{ocr_text[:30000]}\n</ocr>"
            )
        parts: list[dict] = [{"text": full_prompt}]
        if image_bytes is not None:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": mime_type or "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }
                }
            )
        return gcp.vertex_generate_content(
            parts=parts,
            response_schema=schema,
            temperature=0,
        )

    def extract(
        self, image_bytes: bytes, mime_type: str, filename: str
    ) -> BillDraftData:
        from app import gcp

        try:
            ocr_text = gcp.document_ai_ocr(image_bytes, mime_type)
        except Exception:
            # Document AI is optional enrichment; Gemini vision can still read
            # the source image if no processor exists or OCR is unavailable.
            ocr_text = ""
        draft = BillDraftData.model_validate(
            self._request(
                f"{GEMINI_EXTRACTION_PROMPT}\n\nFilename: {filename}",
                BillDraftData.model_json_schema(),
                image_bytes=image_bytes,
                mime_type=mime_type,
                ocr_text=ocr_text,
            )
        )
        recover_items(
            self,
            draft,
            image_bytes=image_bytes,
            mime_type=mime_type,
            ocr_text=ocr_text,
        )
        # The combined pass drops different header fields on each call, so
        # re-read the header whenever any of them is still missing.
        if draft.document_kind == "bill" and not (
            (draft.party.name or "").strip()
            and (draft.bill_number or "").strip()
            and (draft.bill_date or "").strip()
        ):
            try:
                header = BillHeaderRead.model_validate(
                    self._request(
                        HEADER_PROMPT,
                        BillHeaderRead.model_json_schema(),
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        ocr_text=ocr_text,
                    )
                )
                if header.business_name.strip():
                    draft.party.name = header.business_name.strip()
                if not (draft.bill_number or "").strip() and header.bill_number.strip():
                    draft.bill_number = header.bill_number.strip()
                if not (draft.bill_date or "").strip():
                    draft.bill_date = normalize_bill_date(
                        header.bill_date or header.date_text
                    )
            except (requests.RequestException, ValueError):
                pass
        return remove_empty_items(draft)

    def refine(self, draft: BillDraftData, answer: str) -> BillDraftData:
        deterministic = apply_simple_answer(draft, answer)
        if deterministic.model_dump() != draft.model_dump():
            return deterministic
        prompt = (
            f"{REFINEMENT_PROMPT}\n\nCurrent draft:\n"
            f"{draft.model_dump_json()}\n\nUser correction:\n{answer}"
        )
        return remove_empty_items(
            BillDraftData.model_validate(
                self._request(prompt, BillDraftData.model_json_schema())
            )
        )


def _ollama_chat_url() -> str:
    explicit = os.environ.get("BILL_OLLAMA_URL")
    if explicit:
        return explicit
    configured = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    for suffix in ("/v1/chat/completions", "/api/chat"):
        if configured.rstrip("/").endswith(suffix):
            configured = configured.rstrip("/")[: -len(suffix)]
            break
    return configured.rstrip("/") + "/api/chat"


_PARTY_LEAD_IN = re.compile(
    r"\b(?:from|for|to|party|customer|supplier|naam|name)\b"
    r"(?:\s+(?:name|is|ka|ke|hai|=|:))*\s+"
    r"(?P<name>[A-Za-zऀ-ॿ][\wऀ-ॿ&.'\- ]*)",
    re.I,
)
_NAME_STOP_WORDS = {
    "gst", "non", "sale", "sales", "purchase", "paid", "cash", "credit",
    "udhaar", "udhar", "partial", "igst", "cgst", "sgst", "bill", "date",
}


def _rupees_to_paise(raw: str) -> int:
    return int(
        (Decimal(raw) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})\s*(?:st|nd|rd|th)?\s*(?:of\s+)?({_MONTH_PATTERN})\b\.?"
    r"\s*,?\s*(\d{2,4})?",
    re.I,
)
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_PATTERN})\b\.?\s*(\d{{1,2}})\s*(?:st|nd|rd|th)?\s*,?\s*(\d{{2,4}})?",
    re.I,
)


def _dated(day: int, month: int, year_text: str | None) -> str | None:
    """Build a date, defaulting a missing year to the most recent past one."""
    today = date.today()
    if year_text:
        year = int(year_text)
        if year < 100:
            year += 2000
    else:
        year = today.year
    try:
        value = date(year, month, day)
    except ValueError:
        return None
    if not year_text and value > today:
        # A bill is written before it is entered, so an unqualified "18 Jan"
        # after that date this year means last year, never the future.
        try:
            value = date(year - 1, month, day)
        except ValueError:
            return None
    return value.isoformat()


def _find_date(text: str, lower: str) -> str | None:
    """Read a bill date the way a shopkeeper actually writes or says it."""
    iso = re.search(r"\b(20\d{2}-\d{1,2}-\d{1,2})\b", text)
    if iso:
        return normalize_bill_date(iso.group(1))
    numeric = re.search(r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\b", text)
    if numeric:
        return normalize_bill_date(numeric.group(1))
    written = _DAY_MONTH.search(text)
    if written:
        day, month, year = written.groups()
        return _dated(int(day), _MONTHS[month.lower()], year)
    written = _MONTH_DAY.search(text)
    if written:
        month, day, year = written.groups()
        return _dated(int(day), _MONTHS[month.lower()], year)
    if re.search(r"\b(parso|day before yesterday)\b", lower):
        return (date.today() - timedelta(days=2)).isoformat()
    if re.search(r"\b(kal|yesterday|kal ka)\b", lower):
        return (date.today() - timedelta(days=1)).isoformat()
    if re.search(r"\b(today|aaj|aaj ka|today's date)\b", lower):
        return date.today().isoformat()
    return None


def _find_party_name(text: str) -> str | None:
    """Only accept a name introduced by an explicit lead-in word."""
    match = _PARTY_LEAD_IN.search(text)
    if not match:
        return None
    # Stop at the first punctuation break so "from Verma Traders, date ..."
    # never absorbs the rest of a multi-detail sentence.
    name = re.split(r"[,;.\n]", match.group("name"))[0].strip()
    words = [
        word
        for word in name.split()
        if word.lower().strip(".-") not in _NAME_STOP_WORDS
    ]
    name = " ".join(words).strip()
    return name or None


def apply_simple_answer(draft: BillDraftData, answer: str) -> BillDraftData:
    """Deterministic refiner that reads every detail in a single message.

    A shopkeeper who says "purchase from Verma Traders, 3 Jan, non-GST, cash"
    must never be asked those questions again, so this applies everything it
    recognises instead of stopping at the first match. It only ever fills
    values that are still empty: an existing figure is never overwritten and a
    missing one is never guessed.
    """
    updated = draft.model_copy(deep=True)
    text = (answer or "").strip()
    lower = text.lower()
    if not text:
        return updated
    matched = False

    if updated.bill_type is None:
        if re.search(r"\b(purchase|purchased|kharid\w*|khareed\w*)\b", lower):
            updated.bill_type, matched = "purchase", True
        elif re.search(r"\b(sale|sales|sold|bikri|bech\w*)\b", lower):
            updated.bill_type, matched = "sale", True

    if not (updated.bill_date or "").strip():
        found = _find_date(text, lower)
        if found:
            updated.bill_date, matched = found, True

    if updated.gst_mode is None:
        if re.search(r"\b(non[\s-]?gst|without gst|bina gst)\b", lower):
            updated.gst_mode, matched = "non_gst", True
        elif "gst" in lower:
            updated.gst_mode, matched = "gst", True

    if updated.gst_mode == "gst" and updated.gst_rate is None:
        # Require an explicit percent or GST context so a date or phone number
        # can never be read as a tax rate.
        rate = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)|gst\s*(?:@|of|rate)?\s*"
            r"(\d+(?:\.\d+)?)",
            lower,
        )
        if rate:
            updated.gst_rate, matched = (rate.group(1) or rate.group(2)), True
        elif re.fullmatch(r"\d+(?:\.\d+)?", lower):
            updated.gst_rate, matched = lower, True

    if updated.tax_scheme is None and updated.gst_mode == "gst":
        if re.search(r"\b(igst|inter[\s-]?state)\b", lower):
            updated.tax_scheme, matched = "igst", True
        elif re.search(r"\b(cgst|sgst|same state|intra[\s-]?state)\b", lower):
            updated.tax_scheme, matched = "cgst_sgst", True

    if updated.payment_status is None:
        if re.search(r"\b(partial|part payment|aadha|adha)\b", lower):
            updated.payment_status, matched = "partial", True
        elif re.search(r"\b(credit|udhaar|udhar|baaki|baki)\b", lower):
            updated.payment_status, matched = "credit", True
        elif re.search(r"\b(paid|cash|nakad|payment ho gaya|full payment)\b", lower):
            updated.payment_status, matched = "paid", True

    if updated.payment_status == "partial" and updated.paid_amount_paise is None:
        amount = re.search(
            r"(?:rs\.?|inr|₹)\s*(\d+(?:\.\d+)?)"
            r"|(\d+(?:\.\d+)?)\s*(?:rs\.?|rupees|rupaye)"
            r"|(?:paid|diya|jama|partial)\D{0,12}?(\d+(?:\.\d+)?)",
            lower,
        )
        if amount:
            updated.paid_amount_paise = _rupees_to_paise(
                next(group for group in amount.groups() if group)
            )
            matched = True
        elif re.fullmatch(r"\d+(?:\.\d+)?", lower):
            updated.paid_amount_paise, matched = _rupees_to_paise(lower), True

    if not (updated.party.phone or "").strip():
        phone = re.search(r"\b([6-9]\d{9})\b", re.sub(r"[\s-]", "", text))
        if phone:
            updated.party.phone, matched = phone.group(1), True

    if not (updated.party.gstin or "").strip():
        gstin = re.search(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d])\b", text.upper())
        if gstin:
            updated.party.gstin, matched = gstin.group(1), True

    missing_quantities = [
        item for item in updated.items if not (item.quantity or "").strip()
    ]
    if missing_quantities:
        all_one = (
            lower in {"1", "one", "yes", "haan", "ha", "all 1", "all one"}
            or "one each" in lower
            or "1 each" in lower
            or "all are 1" in lower
            or "sab 1" in lower
        )
        if len(missing_quantities) > 1 and all_one:
            for item in missing_quantities:
                item.quantity = "1"
            matched = True
        elif len(missing_quantities) == 1:
            quantity = re.search(r"\b(\d+(?:\.\d+)?)\b", lower)
            if quantity:
                missing_quantities[0].quantity = quantity.group(1)
                matched = True

    if not (updated.party.name or "").strip():
        name = _find_party_name(text)
        if name:
            updated.party.name = name
        elif not matched:
            # Nothing structured was recognised, so a bare reply to the open
            # "what is the party name?" question is the only sensible reading —
            # but only when it actually reads like a name. A rambling sentence
            # must never become a customer record.
            fallback = re.sub(
                r"^(?:party|customer|supplier)(?:\s+name)?\s*(?:is|=|:)?\s*",
                "",
                text,
                flags=re.I,
            ).strip()
            if (
                fallback
                and len(fallback) <= 60
                and len(fallback.split()) <= 5
                and not re.search(r"[?!]", fallback)
            ):
                updated.party.name = fallback
    return updated


def get_extractor(backend: str | None = None) -> BillExtractor:
    # An explicit backend argument is kept for tests and internal maintenance.
    # Normal application traffic follows the private source-code switch.
    selected = (
        backend
        or ("vertex" if config.gcp_enabled() else os.environ.get("BILL_AI_BACKEND", "fake"))
    ).lower()
    if selected == "fake":
        return FakeBillExtractor()
    if selected == "ollama":
        return OllamaBillExtractor()
    if selected == "gemini":
        return GeminiBillExtractor()
    if selected == "vertex":
        return VertexBillExtractor()
    raise ValueError(f"unknown BILL_AI_BACKEND {selected!r}")
