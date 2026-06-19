"""📒 Khata (Ledger) — the structured credits & debits module.

Distinct from the AI Assistant: here the shopkeeper manages customers AND
suppliers with forms — add a party, record a credit/debit, view balances and
history. No AI involved; plain reliable data entry like the product video.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

import _shared as ui  # noqa: E402
import streamlit as st  # noqa: E402

st.set_page_config(page_title="Khata — Dukanbook", page_icon="📒", layout="centered")
ui.inject_theme()
ui.header("📒 Khata", "Credits & debits — customers aur suppliers")

if ui.backend_down_banner():
    st.stop()

parties = ui.list_parties()
id_by_name = {p["name"]: p["id"] for p in parties}
party_names = list(id_by_name)

tab_entry, tab_view, tab_new = st.tabs(["➕ Transaction", "📖 Khaata dekho", "👤 Naya khaata"])

# --- Add a transaction (credit/debit) ---
with tab_entry:
    if not parties:
        st.info("Pehle ‘👤 Naya khaata’ se ek customer/supplier banaiye.")
    else:
        with st.form("txn_form", clear_on_submit=True):
            name = st.selectbox("Kis ka khaata?", party_names)
            ttype = st.radio(
                "Type", ["credit", "debit"], horizontal=True,
                format_func=lambda t: "Udhaar diya (credit)" if t == "credit"
                else "Jama / paisa mila (debit)",
            )
            amount = st.number_input("Rakam (₹)", min_value=1.0, step=10.0)
            note = st.text_input("Note (optional)")
            if st.form_submit_button("Likho"):
                res = ui.add_transaction(id_by_name[name], ttype, amount, note or None)
                if res.status_code == 200:
                    st.success(f"✅ {name}: ₹{amount:.0f} {ttype} likh diya. "
                               f"Naya balance ₹{res.json()['balance']:.0f}.")
                    st.rerun()
                else:
                    st.error(f"Error: {res.text}")

# --- View a party's ledger ---
with tab_view:
    if not parties:
        st.info("Koi khaata nahi hai.")
    else:
        name = st.selectbox("Khaata", party_names, key="view_sel")
        detail = ui.party_detail(id_by_name[name])
        bal = detail["balance"]
        label = "Lene hain" if bal > 0 else ("Dene hain" if bal < 0 else "Clear")
        st.metric(f"{name} — {label}", f"₹{abs(bal):.0f}")
        st.write("**History:**")
        if not detail["transactions"]:
            st.caption("Koi transaction nahi.")
        for t in detail["transactions"]:
            cls = "credit" if t["type"] == "credit" else "debit"
            sign = "+" if t["type"] == "credit" else "−"
            note = f" · {t['note']}" if t["note"] else ""
            st.markdown(
                f"<span class='{cls}'>{sign}₹{t['amount']:.0f}</span> "
                f"<small>({t['type']}){note} · {t['txn_date'][:10]}</small>",
                unsafe_allow_html=True,
            )

# --- Create a new party ---
with tab_new:
    with st.form("party_form", clear_on_submit=True):
        n = st.text_input("Naam")
        t = st.radio("Type", ["customer", "supplier"], horizontal=True)
        ph = st.text_input("Phone (optional)")
        if st.form_submit_button("Khaata banao"):
            if n.strip():
                ui.create_party(n.strip(), t, ph or None)
                st.success(f"✅ {n} ka {t} khaata ban gaya.")
                st.rerun()
            else:
                st.warning("Naam likhiye.")
