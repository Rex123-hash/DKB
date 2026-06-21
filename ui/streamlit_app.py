"""Dukanbook — Dashboard (landing page).

Separate modules live in the sidebar: 📒 Khata (Ledger) and 🤖 AI Assistant.
This page is a read-only overview, like the dashboard in the product video.
"""
from __future__ import annotations

import _shared as ui
import streamlit as st

st.set_page_config(page_title="Dukanbook", page_icon="📒", layout="centered")
ui.inject_theme()
ui.header("📒 Dukanbook", "Aapka digital khata — Dashboard")

if ui.backend_down_banner():
    st.stop()

ui.demo_controls()

parties = ui.list_parties()

receivable = sum(p["balance"] for p in parties if p["balance"] > 0)   # aapko lene hain
payable = sum(-p["balance"] for p in parties if p["balance"] < 0)     # aapko dene hain

pending_reminders = ui.list_reminders("pending")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Khaate", len(parties))
c2.metric("Lene hain (₹)", f"{receivable:.0f}")
c3.metric("Dene hain (₹)", f"{payable:.0f}")
c4.metric("Reminders", len(pending_reminders))

st.divider()
st.subheader("Saare khaate")
if not parties:
    st.info("Abhi koi khaata nahi hai. ‘📒 Khata (Ledger)’ par jaakar pehla khaata banaiye.")
else:
    for p in sorted(parties, key=lambda x: -x["balance"]):
        bal = p["balance"]
        cls = "credit" if bal > 0 else ("debit" if bal < 0 else "")
        st.markdown(
            f"**{p['name']}** · _{p['type']}_ — "
            f"<span class='{cls}'>₹{bal:.0f}</span>",
            unsafe_allow_html=True,
        )

st.divider()
st.caption("👈 Sidebar se ‘Khata (Ledger)’ ya ‘AI Assistant’ kholiye.")
