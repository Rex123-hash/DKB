"""End-to-end API checks for the chatbot-driven bill workflow."""
from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app import db

    monkeypatch.setattr(db, "DEFAULT_DB_PATH", tmp_path / "billing-api.db")
    monkeypatch.setenv("BILL_AI_BACKEND", "fake")
    monkeypatch.setenv("DUKANBOOK_SCAN_DIR", str(tmp_path / "scans"))
    main = importlib.import_module("app.main")
    with TestClient(main.app) as test_client:
        yield test_client


def complete_purchase() -> dict:
    return {
        "bill_type": "purchase",
        "bill_number": "PUR-104",
        "bill_date": "2026-07-29",
        "party": {
            "name": "Sharma Wholesale",
            "phone": "9876543210",
            "gstin": None,
        },
        "gst_mode": "gst",
        "tax_scheme": "cgst_sgst",
        "gst_rate": "5",
        "items": [
            {
                "name": "Rice bag",
                "quantity": "2",
                "unit": "bag",
                "unit_price_paise": 50000,
                "written_total_paise": 100000,
                "hsn": None,
                "gst_rate": None,
                "confidence": {},
            }
        ],
        "discount_paise": 0,
        "extra_charge_paise": 0,
        "round_off_paise": 0,
        "written_subtotal_paise": 100000,
        "written_grand_total_paise": 105000,
        "payment_status": "paid",
        "paid_amount_paise": None,
        "note": "Created through the existing AI Assistant",
        "confidence": {},
    }


def test_scan_review_confirm_updates_every_bill_consumer(client):
    scan = client.post(
        "/bill-drafts/scan",
        files={"file": ("handwritten.jpg", b"\xff\xd8\xfffake-image", "image/jpeg")},
        data={"session_id": "shop-chat-1"},
    )
    assert scan.status_code == 200
    draft = scan.json()
    assert draft["status"] == "needs_information"

    reviewed = client.put(
        f"/bill-drafts/{draft['id']}", json={"data": complete_purchase()}
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "ready_for_review"
    assert reviewed.json()["calculation"]["grand_total_paise"] == 105000

    confirmed = client.post(f"/bill-drafts/{draft['id']}/confirm")
    assert confirmed.status_code == 200
    bill = confirmed.json()
    assert bill["party_name"] == "Sharma Wholesale"
    assert bill["grand_total_paise"] == 105000

    purchases = client.get("/bills?type=purchase").json()
    assert [row["bill_number"] for row in purchases] == ["PUR-104"]
    assert client.get("/bills/summary").json()["total_purchases_paise"] == 105000

    stock = client.get("/stock").json()
    assert stock[0]["name"] == "Rice bag"
    assert stock[0]["quantity"] == "2"

    cash = client.get("/cashbook").json()
    assert cash[0]["direction"] == "out"
    assert cash[0]["amount_paise"] == 105000

    pdf = client.get(f"/bills/{bill['id']}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert len(pdf.content) > 1500


def test_scan_rejects_unsupported_file_type(client):
    response = client.post(
        "/bill-drafts/scan",
        files={"file": ("bill.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 422


def test_scan_rejects_spoofed_image_content(client):
    response = client.post(
        "/bill-drafts/scan",
        files={"file": ("bill.jpg", b"not-an-image", "image/jpeg")},
    )
    assert response.status_code == 422
    assert "does not match" in response.text


def test_confirmation_requires_complete_review(client):
    draft = client.post(
        "/bill-drafts/scan",
        files={"file": ("bill.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
    ).json()
    response = client.post(f"/bill-drafts/{draft['id']}/confirm")
    assert response.status_code == 409


def test_chat_corrections_still_work_after_structured_review(client):
    draft = client.post(
        "/bill-drafts/scan",
        files={"file": ("bill.jpg", b"\xff\xd8\xfffake", "image/jpeg")},
    ).json()
    reviewed_data = complete_purchase()
    reviewed_data["payment_status"] = None
    reviewed = client.put(
        f"/bill-drafts/{draft['id']}", json={"data": reviewed_data}
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["extractor_backend"] == "fake"

    answered = client.post(
        f"/bill-drafts/{draft['id']}/answer", json={"answer": "paid"}
    )
    assert answered.status_code == 200
    assert answered.json()["status"] == "ready_for_review"


def test_bill_summary_and_list_support_parallel_browser_requests(client):
    """The Bills screen fetches its summary and rows with Promise.all."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        summary_future = pool.submit(client.get, "/bills/summary")
        list_future = pool.submit(client.get, "/bills?type=sale")
        summary = summary_future.result()
        bills = list_future.result()
    assert summary.status_code == 200
    assert bills.status_code == 200
