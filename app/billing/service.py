"""Application service for scans, corrections, and finalization."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from io import BytesIO
from pathlib import Path

from app.billing import repository
from app.billing.calculator import validate_bill
from app.billing.extractors import (
    BillExtractor,
    get_extractor,
    remove_empty_items,
)
from app.billing.models import BillDraftData

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_SCAN_BYTES = int(os.environ.get("BILL_MAX_SCAN_BYTES", 10 * 1024 * 1024))
DEFAULT_SCAN_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "bill_scans"
)


def _matches_image_signature(image_bytes: bytes, mime_type: str) -> bool:
    if mime_type == "image/jpeg":
        return image_bytes.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return (
            len(image_bytes) >= 12
            and image_bytes.startswith(b"RIFF")
            and image_bytes[8:12] == b"WEBP"
        )
    return False


def scan_directory() -> Path:
    return Path(os.environ.get("DUKANBOOK_SCAN_DIR", DEFAULT_SCAN_DIR))


VISION_MAX_EDGE = int(os.environ.get("BILL_VISION_MAX_EDGE", "1600"))
VISION_HEAVY_BYTES = int(os.environ.get("BILL_VISION_HEAVY_BYTES", 400 * 1024))


def prepare_for_vision(image_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    """Shrink an oversized photo before it is sent to the model.

    A modern phone photo is far larger than any bill reader needs. Sending it
    whole costs tokens and seconds and is a common cause of read timeouts. The
    original is still what gets stored and shown to the shopkeeper.
    """
    try:
        from PIL import Image
    except ImportError:
        return image_bytes, mime_type
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            oversized = max(image.size) > VISION_MAX_EDGE
            # A screenshot-sized PNG can still be a megabyte. Every provider
            # call re-uploads it, so re-encoding pays for itself several times
            # over on one scan.
            if not oversized and len(image_bytes) <= VISION_HEAVY_BYTES:
                return image_bytes, mime_type
            image = image.convert("RGB")
            if oversized:
                image.thumbnail((VISION_MAX_EDGE, VISION_MAX_EDGE), Image.LANCZOS)
            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=88, optimize=True)
    except Exception:
        # Never let preprocessing stop a scan; the original still works.
        return image_bytes, mime_type
    prepared = buffer.getvalue()
    return (
        (prepared, "image/jpeg")
        if len(prepared) < len(image_bytes)
        else (image_bytes, mime_type)
    )


_ANSWERABLE_FIELDS = (
    "bill_type", "bill_number", "bill_date", "gst_mode", "gst_rate",
    "tax_scheme", "payment_status", "paid_amount_paise", "note",
)


def _merge_over_answers(
    existing: BillDraftData, fresh: BillDraftData
) -> BillDraftData:
    """Keep everything already known, and let the re-read fill only the gaps."""
    merged = fresh.model_copy(deep=True)
    for field in _ANSWERABLE_FIELDS:
        current = getattr(existing, field, None)
        if current not in (None, ""):
            setattr(merged, field, current)
    for part in ("name", "phone", "gstin"):
        current = getattr(existing.party, part, None)
        if current not in (None, ""):
            setattr(merged.party, part, current)
    if existing.items and not merged.items:
        merged.items = [item.model_copy(deep=True) for item in existing.items]
    return merged


def scan_bill(
    conn,
    *,
    image_bytes: bytes,
    filename: str,
    mime_type: str,
    session_id: str,
    extractor: BillExtractor | None = None,
) -> dict:
    if mime_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("bill scan must be a JPEG, PNG, or WebP image")
    if not image_bytes:
        raise ValueError("bill scan is empty")
    if not _matches_image_signature(image_bytes, mime_type):
        raise ValueError("bill scan content does not match its image type")
    if len(image_bytes) > MAX_SCAN_BYTES:
        raise ValueError(
            f"bill scan exceeds the {MAX_SCAN_BYTES // (1024 * 1024)} MB limit"
        )
    session_id = (session_id or "default").strip()[:120] or "default"
    digest = hashlib.sha256(image_bytes).hexdigest()
    duplicate = repository.find_duplicate_draft(conn, session_id, digest)
    provider = extractor or get_extractor()
    provider_version = getattr(provider, "version", None)
    if duplicate is not None:
        if duplicate["status"] != "finalized":
            cached = BillDraftData.model_validate(duplicate.get("data") or {})
            normalized = remove_empty_items(cached)
            if normalized != cached:
                duplicate = repository.save_draft_data(
                    conn,
                    duplicate["id"],
                    normalized,
                    backend=provider.name,
                )
        cached_data = duplicate.get("data") or {}
        cached_kind = cached_data.get("document_kind")
        if (
            duplicate["status"] != "finalized"
            and (
                cached_kind is None
                or (
                    cached_kind == "bill"
                    and provider_version is not None
                    and cached_data.get("extractor_version") != provider_version
                )
                # A bill we failed to read any rows from is not worth serving
                # again. Re-uploading it is the shopkeeper asking us to retry.
                or (cached_kind == "bill" and not cached_data.get("items"))
            )
        ):
            repository.set_draft_status(
                conn, duplicate["id"], "extracting", backend=provider.name
            )
            try:
                vision_bytes, vision_mime = prepare_for_vision(
                    image_bytes, mime_type
                )
                data = remove_empty_items(
                    provider.extract(vision_bytes, vision_mime, filename)
                )
                data.extractor_version = provider_version
                # A re-read must never discard what the shopkeeper already
                # told us; their confirmed answers outrank a fresh guess.
                data = _merge_over_answers(
                    BillDraftData.model_validate(cached_data), data
                )
                refreshed = repository.save_draft_data(
                    conn, duplicate["id"], data, backend=provider.name
                )
                refreshed["duplicate"] = True
                refreshed["reprocessed"] = True
                return refreshed
            except Exception as exc:
                repository.set_draft_status(
                    conn,
                    duplicate["id"],
                    "failed",
                    backend=provider.name,
                    error=str(exc),
                )
                raise
        duplicate["duplicate"] = True
        return duplicate

    draft_id = str(uuid.uuid4())
    folder = scan_directory()
    folder.mkdir(parents=True, exist_ok=True)
    extension = ALLOWED_IMAGE_TYPES[mime_type]
    source_path = folder / f"{draft_id}{extension}"
    source_path.write_bytes(image_bytes)

    repository.create_draft(
        conn,
        draft_id=draft_id,
        session_id=session_id,
        source_filename=Path(filename or f"bill{extension}").name,
        source_mime=mime_type,
        source_path=str(source_path),
        source_sha256=digest,
    )
    repository.set_draft_status(
        conn, draft_id, "extracting", backend=provider.name
    )
    try:
        vision_bytes, vision_mime = prepare_for_vision(image_bytes, mime_type)
        data = remove_empty_items(
            provider.extract(vision_bytes, vision_mime, filename)
        )
        data.extractor_version = provider_version
        result = repository.save_draft_data(
            conn, draft_id, data, backend=provider.name
        )
        result["duplicate"] = False
        return result
    except Exception as exc:
        repository.set_draft_status(
            conn, draft_id, "failed", backend=provider.name, error=str(exc)
        )
        raise


def replace_draft_data(conn, draft_id: str, data: BillDraftData | dict) -> dict:
    # Preserve the original extractor so the user can return to natural-language
    # or voice corrections after making an exact structured edit.
    validated = remove_empty_items(BillDraftData.model_validate(data))
    if validated.document_kind in (None, "uncertain"):
        # Reaching the exact review form is an explicit human assertion that
        # the uploaded document is intended to become a bill.
        validated.document_kind = "bill"
        validated.document_reason = None
    return repository.save_draft_data(conn, draft_id, validated, backend=None)


def answer_draft(
    conn,
    draft_id: str,
    answer: str,
    *,
    extractor: BillExtractor | None = None,
) -> dict:
    record = repository.get_draft(conn, draft_id)
    data = remove_empty_items(BillDraftData.model_validate(record["data"]))
    normalized_answer = " ".join((answer or "").lower().split())
    if normalized_answer in {
        "none",
        "no",
        "skip",
        "not applicable",
        "na",
        "n/a",
        "no missing item",
        "no missing items",
    }:
        calculation = validate_bill(data)
        first_missing = (
            calculation.missing_fields[0]
            if calculation.missing_fields
            else None
        )
        if (
            first_missing
            and re.fullmatch(r"items\.\d+\.name", first_missing)
        ):
            try:
                item_index = int(first_missing.split(".")[1])
                data.items.pop(item_index)
            except (ValueError, IndexError):
                pass
        return repository.save_draft_data(
            conn,
            draft_id,
            remove_empty_items(data),
            backend=record.get("extractor_backend"),
        )
    provider = extractor or get_extractor(record.get("extractor_backend"))
    updated = remove_empty_items(provider.refine(data, answer))
    result = repository.save_draft_data(
        conn, draft_id, updated, backend=record.get("extractor_backend")
    )
    # Tell the caller whether the reply actually changed anything, so the
    # assistant can say "I did not catch that" instead of silently repeating
    # the same question and sounding broken.
    before, after = data.model_dump(), updated.model_dump()
    result["answer_applied"] = after != before
    # Naming what was just understood is what makes the assistant sound like it
    # is listening rather than working through a form.
    result["applied_fields"] = [
        field
        for field in ("bill_type", "bill_date", "gst_mode", "gst_rate",
                      "tax_scheme", "payment_status", "paid_amount_paise")
        if before.get(field) != after.get(field) and after.get(field) is not None
    ] + [
        f"party.{part}"
        for part in ("name", "phone", "gstin")
        if before["party"].get(part) != after["party"].get(part)
        and after["party"].get(part) is not None
    ]
    return result
