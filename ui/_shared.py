"""Shared theme + API helpers for the Dukanbook Streamlit modules."""
from __future__ import annotations

import os

import requests
import streamlit as st

API_URL = os.environ.get("DUKANBOOK_API", "http://127.0.0.1:8000")


def inject_theme() -> None:
    """Warm bahi-khata styling. NO purple/violet/blue."""
    st.markdown(
        """
        <style>
          :root {
            --ledger-green: #1F6E43;
            --saffron: #E08A1E;
            --paper: #FBF7EF;
            --ink: #2B2620;
            --red: #B23A2E;
          }
          .stApp { background-color: var(--paper); }
          .khata-header { border-bottom: 3px double var(--ledger-green);
            padding-bottom: .35rem; margin-bottom: 1rem; }
          .khata-header h1 { color: var(--ledger-green); margin-bottom: 0; font-family: serif; }
          .khata-header p  { color: var(--ink); opacity: .8; margin-top: .2rem; }
          .stButton>button, .stChatInput button, .stFormSubmitButton>button {
            background: var(--saffron); color: #fff; border: none; }
          .credit { color: var(--ledger-green); font-weight: 700; }
          .debit  { color: var(--red); font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def header(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="khata-header"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


# ---- API helpers ----
def _get(path: str):
    return requests.get(f"{API_URL}{path}", timeout=30)


def _post(path: str, payload: dict):
    return requests.post(f"{API_URL}{path}", json=payload, timeout=60)


def health() -> dict:
    try:
        return _get("/health").json()
    except requests.RequestException:
        return {"status": "down", "llm": False}


def list_parties() -> list[dict]:
    try:
        return _get("/parties").json()
    except requests.RequestException:
        return []


def create_party(name: str, type: str, phone: str | None):
    return _post("/parties", {"name": name, "type": type, "phone": phone}).json()


def add_transaction(party_id: int, type: str, amount: float, note: str | None):
    return _post("/transactions", {"party_id": party_id, "type": type,
                                   "amount": amount, "note": note})


def party_detail(party_id: int) -> dict:
    return _get(f"/parties/{party_id}").json()


def chat(message: str) -> dict:
    return _post("/chat", {"message": message}).json()


def voice_chat(audio_bytes: bytes, filename: str = "audio.wav") -> dict:
    files = {"file": (filename, audio_bytes, "audio/wav")}
    return requests.post(f"{API_URL}/voice/chat", files=files, timeout=120).json()


def list_reminders(status: str = "pending") -> list[dict]:
    try:
        return _get(f"/reminders?status={status}").json()
    except requests.RequestException:
        return []


def create_reminder(party_id: int, due_at: str, message: str | None):
    return _post("/reminders", {"party_id": party_id, "due_at": due_at, "message": message})


def reminder_done(reminder_id: int):
    return requests.post(f"{API_URL}/reminders/{reminder_id}/done", timeout=30)


def seed_demo():
    return requests.post(f"{API_URL}/admin/seed", timeout=30).json()


def reset_demo():
    return requests.post(f"{API_URL}/admin/reset", timeout=30).json()


def demo_controls() -> None:
    """Sidebar buttons to load sample data or clear everything (for demos)."""
    with st.sidebar:
        st.markdown("---")
        st.caption("Demo controls")
        if st.button("🌱 Load sample data", use_container_width=True):
            seed_demo()
            st.rerun()
        if st.button("🧹 Reset (clear all)", use_container_width=True):
            reset_demo()
            st.rerun()


def backend_down_banner() -> bool:
    """Show a warning if the backend isn't reachable. Returns True if down."""
    if health().get("status") != "ok":
        st.error(f"Backend ({API_URL}) se connection nahi ho paaya. "
                 "Backend chalaiye:  uvicorn app.main:app --port 8000")
        return True
    return False
