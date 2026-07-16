"""Shared theme + API helpers for the Dukanbook Streamlit modules."""
from __future__ import annotations

import os
import uuid

import requests
import streamlit as st

API_URL = os.environ.get("DUKANBOOK_API", "http://127.0.0.1:8000")


def inject_theme() -> None:
    """NestDukanBook brand styling — matches the nestdukanbook.com web app:
    teal brand bar + accents, green=you-will-get, red=you-will-give, clean white."""
    st.markdown(
        """
        <style>
          :root {
            --brand:      #13C2C2;   /* DukanBook teal */
            --brand-dark: #0EA5A5;
            --brand-soft: #E6FAFA;
            --get:        #1A9E5A;   /* you will get (credit) */
            --give:       #E0392B;   /* you will give (debit) */
            --ink:        #1F2A2A;
            --muted:      #6B7A7A;
            --surface:    #FFFFFF;
            --bg:         #F4F8F8;
          }
          .stApp { background-color: var(--bg); }
          /* ---- brand app bar (mimics the website's teal header) ---- */
          .db-appbar {
            background: linear-gradient(180deg, var(--brand) 0%, var(--brand-dark) 100%);
            border-radius: 16px; padding: 16px 22px; margin-bottom: 1.1rem;
            box-shadow: 0 4px 14px rgba(19,194,194,.25);
            display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
          }
          .db-appbar .db-brand { color: #06363A; font-weight: 800; font-size: 1.55rem;
            letter-spacing: -.5px; line-height: 1; }
          .db-appbar .db-page  { color: #06484C; font-weight: 700; font-size: 1.05rem; }
          .db-appbar .db-tag   { color: rgba(3,54,58,.78); font-size: .9rem; margin-left: auto; }
          /* ---- summary pill (You will get / You will give) ---- */
          .db-pill { background: #F2F6F6; border: 1px solid #E2EBEB; border-radius: 14px;
            padding: 14px 8px; display: flex; }
          .db-pill .col { flex: 1; text-align: center; }
          .db-pill .amt { font-size: 1.5rem; font-weight: 800; line-height: 1.1; }
          .db-pill .lbl { font-size: .82rem; color: var(--muted); margin-top: 2px; }
          .db-pill .get .amt { color: var(--get); }
          .db-pill .give .amt { color: var(--give); }
          /* ---- buttons / inputs in brand teal ---- */
          .stButton>button, .stFormSubmitButton>button, .stChatInput button,
          .stDownloadButton>button, .stLinkButton>a {
            background: var(--brand); color: #fff; border: none; border-radius: 10px;
            font-weight: 600; }
          .stButton>button:hover, .stFormSubmitButton>button:hover,
          .stLinkButton>a:hover { background: var(--brand-dark); color: #fff; }
          .stTabs [aria-selected="true"] { color: var(--brand-dark) !important; }
          .stTabs [data-baseweb="tab-highlight"] { background-color: var(--brand) !important; }
          [data-testid="stMetric"] { background: var(--surface); border: 1px solid #E2EBEB;
            border-radius: 12px; padding: 12px 14px; }
          [data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within {
            border-color: var(--brand) !important; }
          .credit { color: var(--get); font-weight: 700; }
          .debit  { color: var(--give); font-weight: 700; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def header(title: str, subtitle: str) -> None:
    """Render the teal brand bar with the DukanBook wordmark + the page name."""
    st.markdown(
        f'<div class="db-appbar">'
        f'<span class="db-brand">DukanBook</span>'
        f'<span class="db-page">{title}</span>'
        f'<span class="db-tag">{subtitle}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def summary_pill(get_amount: float, give_amount: float) -> None:
    """The website's 'You will get / You will give' summary, in brand colors."""
    st.markdown(
        f'<div class="db-pill">'
        f'<div class="col get"><div class="amt">₹{get_amount:,.0f}</div>'
        f'<div class="lbl">You will get · Lene hain</div></div>'
        f'<div class="col give"><div class="amt">₹{give_amount:,.0f}</div>'
        f'<div class="lbl">You will give · Dene hain</div></div>'
        f'</div>',
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


def set_party_phone(party_id: int, phone: str):
    return _post(f"/parties/{party_id}/phone", {"phone": phone})


def add_transaction(party_id: int, type: str, amount: float, note: str | None):
    return _post("/transactions", {"party_id": party_id, "type": type,
                                   "amount": amount, "note": note})


def party_detail(party_id: int) -> dict:
    return _get(f"/parties/{party_id}").json()


def session_id() -> str:
    """A stable id for this browser session, so multi-turn dialogs (e.g. the
    account-creation phone prompt) are scoped per user."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = uuid.uuid4().hex
    return st.session_state.session_id


def chat(message: str) -> dict:
    return _post("/chat", {"message": message, "session_id": session_id()}).json()


def voice_chat(audio_bytes: bytes, filename: str = "audio.wav") -> dict:
    files = {"file": (filename, audio_bytes, "audio/wav")}
    data = {"session_id": session_id()}
    return requests.post(f"{API_URL}/voice/chat", files=files, data=data, timeout=120).json()


def list_reminders(status: str = "pending") -> list[dict]:
    try:
        return _get(f"/reminders?status={status}").json()
    except requests.RequestException:
        return []


def create_reminder(party_id: int, due_at: str, message: str | None,
                    amount: float | None = None, channel: str = "call"):
    return _post("/reminders", {"party_id": party_id, "due_at": due_at, "message": message,
                                "amount": amount, "channel": channel})


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
