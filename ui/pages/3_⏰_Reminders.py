"""⏰ Reminders — payment & call reminders module (separate from Khata & AI).

Create a reminder for a customer/supplier, see what's pending or due, mark done.
The AI Assistant can also create reminders by voice/text ("Ramesh ko kal yaad
dilao") — they show up here too (shared DB).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import _shared as ui  # noqa: E402
import streamlit as st  # noqa: E402

st.set_page_config(page_title="Reminders — Dukanbook", page_icon="⏰", layout="centered")
ui.inject_theme()
ui.header("⏰ Reminders", "Payment aur call reminders — customers/suppliers ke liye")

if ui.backend_down_banner():
    st.stop()

parties = ui.list_parties()
id_by_name = {p["name"]: p["id"] for p in parties}

# --- create a reminder ---
with st.expander("➕ Naya reminder", expanded=not parties or True):
    if not parties:
        st.info("Pehle ‘📒 Khata’ mein ek customer/supplier banaiye.")
    else:
        with st.form("rem_form", clear_on_submit=True):
            name = st.selectbox("Kis ke liye?", list(id_by_name))
            col1, col2 = st.columns(2)
            d = col1.date_input("Date", value=dt.date.today() + dt.timedelta(days=1))
            t = col2.time_input("Time", value=dt.time(10, 0))
            col3, col4 = st.columns(2)
            amount = col3.number_input("Amount (₹, optional)", min_value=0.0, step=100.0)
            channel = col4.radio("Channel", ["call", "whatsapp"], horizontal=True)
            msg = st.text_input("Message", value="Payment yaad dilao")
            if st.form_submit_button("Set reminder"):
                due = dt.datetime.combine(d, t).isoformat(timespec="minutes")
                res = ui.create_reminder(id_by_name[name], due, msg or None,
                                         amount=amount or None, channel=channel)
                if res.status_code == 200:
                    st.success(f"⏰ {name} ke liye reminder set: {d} {t.strftime('%H:%M')}")
                    st.rerun()
                else:
                    st.error(res.text)

st.divider()

# --- pending reminders ---
pending = ui.list_reminders("pending")
now = dt.datetime.now()
st.subheader(f"Pending ({len(pending)})")
if not pending:
    st.caption("Koi pending reminder nahi.")
for r in pending:
    try:
        due = dt.datetime.fromisoformat(r["due_at"])
        overdue = due <= now
        when = due.strftime("%d %b %Y, %H:%M")
    except ValueError:
        overdue, when = False, r["due_at"]
    flag = "🔴 DUE" if overdue else "🟢"
    amt = r.get("amount")
    amt_txt = f" · ₹{amt:.0f}" if amt else ""
    ch_txt = " · 📲 whatsapp" if r.get("channel") == "whatsapp" else " · 📞 call"
    c1, c2, c3, c4 = st.columns([4, 1, 1, 1])
    c1.markdown(f"{flag}  **{r['party_name']}**{amt_txt} — {r.get('message') or 'reminder'}  \n"
                f"<small>{when}{ch_txt}</small>", unsafe_allow_html=True)
    if r.get("call_link"):
        c2.link_button("📞 Call", r["call_link"])
    if r.get("whatsapp_link"):
        c3.link_button("📲 WhatsApp", r["whatsapp_link"])
    if c4.button("Done", key=f"done_{r['id']}"):
        ui.reminder_done(r["id"])
        st.rerun()
