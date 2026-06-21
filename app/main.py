"""FastAPI backend for the Dukanbook AI assistant.

Two clearly separated surfaces:
  * Structured Ledger API  — /parties, /transactions  (the Khata module)
  * AI Assistant           — /chat                     (the smart assistant)
Both read/write the SAME SQLite DB, so data stays consistent across modules.
"""
from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app import brain, config, db, demo_data, rag, tools, voice  # noqa: F401  (config loads .env)


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
    # Build the knowledge base once (first run downloads the embed model).
    try:
        if rag.count(conn) == 0:
            n = rag.ingest(conn)
            print(f"[rag] ingested {n} knowledge chunks")
    except Exception as e:  # never block startup on RAG
        print(f"[rag] ingest skipped: {e}")
    conn.close()
    yield


app = FastAPI(title="Dukanbook AI Assistant", version="0.2.0", lifespan=lifespan)


# ---- models ----
class ChatRequest(BaseModel):
    message: str
    lang: str = "auto"


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


# ---- health ----
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm": config.has_llm(), "voice": config.has_voice()}


# ---- AI Assistant (separate module) ----
@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    return ChatResponse(reply=brain.respond(req.message, req.lang), llm=config.has_llm())


@app.post("/voice/chat")
def voice_chat(file: UploadFile = File(...)) -> dict:
    """Audio in -> transcribe -> brain -> reply (+ TTS audio out).

    Sync route: FastAPI runs it in a worker thread, so the blocking Whisper call
    and edge-tts's asyncio.run both work without touching the main event loop.
    """
    if not config.has_voice():
        raise HTTPException(status_code=503, detail="voice libraries not installed")
    audio = file.file.read()
    stt = voice.transcribe(audio, file.filename or "audio.wav")
    reply = brain.respond(stt["text"], stt["lang"])
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
    return [dict(r) for r in db.list_reminders(conn, status)]


@app.post("/reminders")
def create_reminder(r: ReminderIn, conn=Depends(get_conn)) -> dict:
    if db.get_party(conn, r.party_id) is None:
        raise HTTPException(status_code=404, detail="party not found")
    rid = db.add_reminder(conn, r.party_id, r.due_at, r.message)
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
