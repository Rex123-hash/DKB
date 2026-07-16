"""Dukanbook — Dashboard (landing page).

Separate modules live in the sidebar: 📒 Khata (Ledger) and 🤖 AI Assistant.
This page is a read-only overview, like the dashboard in the product video.
"""
from __future__ import annotations

import _shared as ui
import streamlit as st

st.set_page_config(page_title="Dukanbook", page_icon="📒", layout="centered")
ui.inject_theme()
ui.header("Dashboard", "Aapka digital khata")

if ui.backend_down_banner():
    st.stop()

ui.demo_controls()

parties = ui.list_parties()

receivable = sum(p["balance"] for p in parties if p["balance"] > 0)   # aapko lene hain
payable = sum(-p["balance"] for p in parties if p["balance"] < 0)     # aapko dene hain

pending_reminders = ui.list_reminders("pending")

ui.summary_pill(receivable, payable)

c1, c2 = st.columns(2)
c1.metric("Khaate", len(parties))
c2.metric("Reminders", len(pending_reminders))

st.divider()
st.subheader("Saare khaate")
if not parties:
    st.info("Abhi koi khaata nahi hai. ‘📒 Khata (Ledger)’ par jaakar pehla khaata banaiye.")
else:
    for p in sorted(parties, key=lambda x: -x["balance"]):
        bal = p["balance"]
        cls = "credit" if bal > 0 else ("debit" if bal < 0 else "")
        phone_txt = f" · 📞 {p['phone']}" if p.get("phone") else ""
        st.markdown(
            f"**{p['name']}** · _{p['type']}_{phone_txt} — "
            f"<span class='{cls}'>₹{bal:.0f}</span>",
            unsafe_allow_html=True,
        )

st.divider()
st.caption("👈 Sidebar se ‘Khata (Ledger)’ ya ‘AI Assistant’ kholiye.")
