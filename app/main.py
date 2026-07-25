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
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import brain, config, db, demo_data, rag, tools, voice  # noqa: F401  (config loads .env)

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


app = FastAPI(title="Dukanbook AI Assistant", version="0.2.0", lifespan=lifespan)


# ---- models ----
class ChatRequest(BaseModel):
    message: str
    lang: str = "auto"
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    llm: bool = False


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
    amount: float | None = None
    channel: Literal["call", "whatsapp"] = "call"


# ---- health ----
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm": config.has_llm(), "voice": config.has_voice()}


# ---- AI Assistant (separate module) ----
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return ChatResponse(
        reply=brain.respond(req.message, req.lang, session_id=req.session_id),
        llm=config.has_llm(),
    )


@app.post("/voice/chat")
def voice_chat(file: UploadFile = File(...), session_id: str = Form("default")) -> dict:
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
    stt = voice.transcribe(audio, file.filename or "audio.wav", voice.build_hint(names))
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
    rid = db.add_reminder(conn, r.party_id, r.due_at, r.message, amount=r.amount,
                          channel=r.channel, phone=party["phone"])
    return {"id": rid}


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
