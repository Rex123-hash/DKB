"""Application service for scans, corrections, and finalization."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
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
        if (
            duplicate["status"] != "finalized"
            and (
                duplicate.get("data", {}).get("document_kind") is None
                or (
                    duplicate.get("data", {}).get("document_kind") == "bill"
                    and provider_version is not None
                    and duplicate.get("data", {}).get("extractor_version")
                    != provider_version
                )
            )
        ):
            repository.set_draft_status(
                conn, duplicate["id"], "extracting", backend=provider.name
            )
            try:
                data = remove_empty_items(
                    provider.extract(image_bytes, mime_type, filename)
                )
                data.extractor_version = provider_version
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
        data = remove_empty_items(
            provider.extract(image_bytes, mime_type, filename)
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
    updated = provider.refine(data, answer)
    return repository.save_draft_data(
        conn, draft_id, updated, backend=record.get("extractor_backend")
    )
