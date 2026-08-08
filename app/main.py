"""FastAPI backend for the Dukanbook AI assistant.

Two clearly separated surfaces:
  * Structured Ledger API  — /parties, /transactions  (the Khata module)
  * AI Assistant           — /chat                     (the smart assistant)
Both read/write the SAME SQLite DB, so data stays consistent across modules.
"""
from __future__ import annotations

import base64
import os
from contextlib import asynccontextmanager
from typing import Literal

import pathlib

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import brain, config, db, demo_data, rag, tools, voice  # noqa: F401  (config loads .env)
from app.billing import models as billing_models
from app.billing import pdf as billing_pdf
from app.billing import repository as billing_repository
from app.billing import service as billing_service

WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"


def get_conn():
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.get_connection()
    db.init_db(conn)
    # Load the knowledge base once. Prefer the prebuilt vector cache so a fresh
    # deployment starts instantly and spends no embedding quota; fall back to
    # embedding from source when there is no cache (first local run).
    try:
        if rag.count(conn) == 0:
            n = rag.load_kb(conn)
            if n:
                print(f"[rag] loaded {n} knowledge chunks from cache")
            else:
                n = rag.ingest(conn)
                print(f"[rag] ingested {n} knowledge chunks")
    except Exception as e:  # never block startup on RAG
        print(f"[rag] knowledge base unavailable: {e}")
    # On a fresh deployment the ledger is empty, which makes the live demo look
    # broken. Seed once when asked, and only while there is nothing to lose.
    if os.environ.get("SEED_ON_START") == "1":
        try:
            if not db.list_parties(conn):
                demo_data.seed(conn)
                print("[seed] loaded demo shop data")
        except Exception as e:
            print(f"[seed] skipped: {e}")
    conn.close()
    yield


app = FastAPI(title="Dukanbook AI Assistant", version="0.3.0", lifespan=lifespan)


# ---- models ----
class ChatRequest(BaseModel):
    message: str
    lang: str = "auto"
    session_id: str = "default"
    speak: bool = False


class ChatResponse(BaseModel):
    reply: str
    llm: bool = False
    audio_b64: str | None = None


class PartyIn(BaseModel):
    name: str = Field(min_length=1)
    type: Literal["customer", "supplier"] = "customer"
    phone: str | None = None


class TransactionIn(BaseModel):
    party_id: int
    type: Literal["credit", "debit"]
    amount: float = Field(gt=0)
    note: str | None = None


class ReminderIn(BaseModel):
    party_id: int
    due_at: str
    message: str | None = None
    amount: float = Field(gt=0)
    channel: Literal["call", "whatsapp"] = "call"
    skip_phone: bool = False


class BillDraftReplaceIn(BaseModel):
    data: billing_models.BillDraftData


class BillDraftAnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


# ---- health ----
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm": config.has_llm(), "voice": config.has_voice()}


# ---- AI Assistant (separate module) ----
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    reply = brain.respond(req.message, req.lang, session_id=req.session_id)
    audio_b64 = None
    if req.speak and config.has_voice():
        # Speak-while-typing is opt-in. A TTS failure must never cost the user
        # their written answer, so the text reply is returned either way.
        try:
            out = voice.synthesize(reply, req.lang if req.lang != "auto" else "hi")
            if out:
                audio_b64 = base64.b64encode(out).decode()
        except Exception:
            pass
    return ChatResponse(reply=reply, llm=config.has_llm(), audio_b64=audio_b64)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    lang: str = "hi"


@app.post("/speak")
def speak(req: SpeakRequest) -> dict:
    """Speak a reply the client composed itself.

    Bill answers are assembled in the browser, so without this the assistant
    falls silent exactly when the shopkeeper is scanning a bill.
    """
    if not config.has_voice():
        raise HTTPException(status_code=503, detail="voice libraries not installed")
    try:
        out = voice.synthesize(req.text, req.lang or "hi")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"speech failed: {exc}") from exc
    return {"audio_b64": base64.b64encode(out).decode() if out else None}


@app.post("/voice/chat")
def voice_chat(
    file: UploadFile = File(...),
    session_id: str = Form("default"),
    duration_ms: int = Form(0),
) -> dict:
    """Audio in -> transcribe -> brain -> reply (+ TTS audio out).

    Sync route: FastAPI runs it in a worker thread, so the blocking Whisper call
    and edge-tts's asyncio.run both work without touching the main event loop.
    """
    if not config.has_voice():
        raise HTTPException(status_code=503, detail="voice libraries not installed")
    audio = file.file.read()
    # Feed the recogniser this shop's own party names, so spoken names come
    # back spelled the way they are stored instead of phonetically.
    conn = db.get_connection()
    try:
        names = [r["name"] for r in db.list_parties(conn)]
    except Exception:
        names = []
    finally:
        conn.close()
    try:
        transcribe_args = (
            audio,
            file.filename or "audio.wav",
            voice.build_hint(names),
        )
        # Keep the original three-argument contract for existing callers that
        # do not send duration metadata.
        stt = (
            voice.transcribe(*transcribe_args, duration_ms=duration_ms)
            if duration_ms
            else voice.transcribe(*transcribe_args)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"voice transcription failed: {exc}") from exc
    reply = brain.respond(stt["text"], stt["lang"], session_id=session_id)
    audio_b64 = None
    try:
        out = voice.synthesize(reply, stt["lang"])
        if out:
            audio_b64 = base64.b64encode(out).decode()
    except Exception:
        pass  # TTS failure shouldn't drop the text reply
    return {
        "transcript": stt["text"],
        "lang": stt["lang"],
        "reply": reply,
        "audio_b64": audio_b64,
        "llm": config.has_llm(),
    }


# ---- Structured Ledger / Khata module ----
@app.get("/parties")
def list_parties(conn=Depends(get_conn)) -> list[dict]:
    return tools.list_all_parties(conn)


@app.post("/parties")
def create_party(p: PartyIn, conn=Depends(get_conn)) -> dict:
    pid = db.add_party(conn, p.name, p.type, p.phone)
    return {"id": pid, "name": p.name, "type": p.type}


class PhoneIn(BaseModel):
    phone: str


@app.post("/parties/{party_id}/phone")
def set_party_phone(party_id: int, body: PhoneIn, conn=Depends(get_conn)) -> dict:
    if db.get_party(conn, party_id) is None:
        raise HTTPException(status_code=404, detail="party not found")
    normalized = tools.normalize_phone(body.phone)
    if normalized is None:
        raise HTTPException(status_code=422, detail="invalid phone (need 10-digit Indian mobile)")
    db.set_party_phone(conn, party_id, normalized)
    return {"id": party_id, "phone": normalized}


@app.post("/transactions")
def add_transaction(t: TransactionIn, conn=Depends(get_conn)) -> dict:
    if db.get_party(conn, t.party_id) is None:
        raise HTTPException(status_code=404, detail="party not found")
    db.add_transaction(conn, t.party_id, t.type, t.amount, note=t.note)
    return {"party_id": t.party_id, "balance": db.get_balance(conn, t.party_id)}


# ---- AI bill scanning / review / posting ----
@app.post("/bill-drafts/scan")
def scan_bill_draft(
    file: UploadFile = File(...),
    session_id: str = Form("default"),
    conn=Depends(get_conn),
) -> dict:
    """Image -> persistent AI bill draft. Never posts accounting side effects."""
    content_type = (file.content_type or "").split(";")[0].lower()
    image = file.file.read(billing_service.MAX_SCAN_BYTES + 1)
    try:
        return billing_service.scan_bill(
            conn,
            image_bytes=image,
            filename=file.filename or "bill.jpg",
            mime_type=content_type,
            session_id=session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"bill extraction failed: {exc}"
        ) from exc


@app.get("/bill-drafts")
def list_bill_drafts(
    session_id: str | None = None,
    limit: int = 25,
    conn=Depends(get_conn),
) -> list[dict]:
    return billing_repository.list_drafts(conn, session_id, limit)


@app.get("/bill-drafts/{draft_id}")
def get_bill_draft(draft_id: str, conn=Depends(get_conn)) -> dict:
    try:
        return billing_repository.get_draft(conn, draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/bill-drafts/{draft_id}")
def replace_bill_draft(
    draft_id: str, body: BillDraftReplaceIn, conn=Depends(get_conn)
) -> dict:
    """Exact structured edits from the review screen."""
    try:
        return billing_service.replace_draft_data(conn, draft_id, body.data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/bill-drafts/{draft_id}/answer")
def answer_bill_draft(
    draft_id: str, body: BillDraftAnswerIn, conn=Depends(get_conn)
) -> dict:
    """Natural-language correction supplied through the AI chat."""
    try:
        return billing_service.answer_draft(conn, draft_id, body.answer)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"bill correction failed: {exc}"
        ) from exc


@app.post("/bill-drafts/{draft_id}/voice-answer")
def voice_answer_bill_draft(
    draft_id: str,
    file: UploadFile = File(...),
    duration_ms: int = Form(0),
    conn=Depends(get_conn),
) -> dict:
    """Audio correction -> transcript -> update the persistent bill draft."""
    if not config.has_voice():
        raise HTTPException(status_code=503, detail="voice libraries not installed")
    audio = file.file.read()
    try:
        names = [row["name"] for row in db.list_parties(conn)]
        transcribe_args = (
            audio,
            file.filename or "audio.webm",
            voice.build_hint(names),
        )
        transcript = (
            voice.transcribe(*transcribe_args, duration_ms=duration_ms)
            if duration_ms
            else voice.transcribe(*transcribe_args)
        )
        draft = billing_service.answer_draft(
            conn, draft_id, transcript["text"]
        )
        return {"transcript": transcript, "draft": draft}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"voice correction failed: {exc}"
        ) from exc


@app.post("/bill-drafts/{draft_id}/confirm")
def confirm_bill_draft(draft_id: str, conn=Depends(get_conn)) -> dict:
    """Explicit confirmation: atomically post bill, stock, ledger, and cash."""
    try:
        return billing_repository.finalize_draft(conn, draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/bills/summary")
def get_bills_summary(conn=Depends(get_conn)) -> dict:
    return billing_repository.bill_summary(conn)


@app.get("/bills")
def list_bills(
    type: Literal["sale", "purchase"] | None = None,
    limit: int = 100,
    conn=Depends(get_conn),
) -> list[dict]:
    return billing_repository.list_bills(conn, type, limit)


@app.get("/bills/{bill_id}")
def get_bill(bill_id: int, conn=Depends(get_conn)) -> dict:
    try:
        return billing_repository.get_bill(conn, bill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/bills/{bill_id}")
def delete_bill(bill_id: int, conn=Depends(get_conn)) -> dict:
    """Delete a wrongly entered bill and reverse everything it posted."""
    try:
        return billing_repository.delete_bill(conn, bill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/bills/{bill_id}/pdf")
def download_bill_pdf(bill_id: int, conn=Depends(get_conn)) -> Response:
    """Download a professional DukanBook-branded finalized bill."""
    try:
        bill = billing_repository.get_bill(conn, bill_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    content = billing_pdf.build_bill_pdf(bill)
    safe_number = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in bill["bill_number"]
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="DukanBook-{safe_number}.pdf"'
            )
        },
    )


@app.get("/stock")
def list_stock(conn=Depends(get_conn)) -> list[dict]:
    return billing_repository.list_stock(conn)


@app.get("/cashbook")
def list_cashbook(limit: int = 100, conn=Depends(get_conn)) -> list[dict]:
    return billing_repository.list_cashbook(conn, limit)


# ---- Demo controls ----
@app.post("/admin/seed")
def admin_seed(conn=Depends(get_conn)) -> dict:
    return {"status": "seeded", **demo_data.seed(conn)}


@app.post("/admin/reset")
def admin_reset(conn=Depends(get_conn)) -> dict:
    demo_data.reset(conn)
    return {"status": "reset"}


# ---- Reminders module ----
@app.get("/reminders")
def list_reminders(status: str = "pending", conn=Depends(get_conn)) -> list[dict]:
    out = []
    for r in db.list_reminders(conn, status):
        row = dict(r)
        row["whatsapp_link"] = tools.whatsapp_link(
            row.get("phone"),
            tools._reminder_text(row["party_name"], row.get("amount"), row.get("message")),
        )
        row["call_link"] = tools.call_link(row.get("phone"))
        out.append(row)
    return out


@app.post("/reminders")
def create_reminder(r: ReminderIn, conn=Depends(get_conn)) -> dict:
    party = db.get_party(conn, r.party_id)
    if party is None:
        raise HTTPException(status_code=404, detail="party not found")
    result = tools.schedule_reminder(
        conn, party["name"], r.due_at, r.message, amount=r.amount,
        channel=r.channel, skip_phone=r.skip_phone,
    )
    if result.get("needs_amount"):
        raise HTTPException(status_code=409, detail="positive reminder amount is required")
    if result.get("needs_phone"):
        raise HTTPException(
            status_code=409,
            detail="10-digit phone is required; provide one or explicitly skip",
        )
    return {"id": result["id"]}


@app.post("/reminders/{reminder_id}/done")
def reminder_done(reminder_id: int, conn=Depends(get_conn)) -> dict:
    db.mark_reminder_done(conn, reminder_id)
    return {"id": reminder_id, "status": "done"}


@app.get("/parties/{party_id}")
def party_detail(party_id: int, conn=Depends(get_conn)) -> dict:
    row = db.get_party(conn, party_id)
    if row is None:
        raise HTTPException(status_code=404, detail="party not found")
    txns = [dict(r) for r in db.get_transactions(conn, party_id)]
    return {
        "party": dict(row),
        "balance": db.get_balance(conn, party_id),
        "transactions": txns,
    }


# ---- Branded web app (HTML/CSS/JS replica of nestdukanbook.com), served same-origin ----
if WEB_DIR.is_dir():
    # The deployed root should open the shop app, not the bare API.
    @app.get("/", include_in_schema=False)
    def _root() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    app.mount("/app", StaticFiles(directory=str(WEB_DIR), html=True), name="webapp")
