"""The brain: turn a user message into a reply.

Phase 1: real ledger handling. If GROQ_API_KEY is set, the Groq tool-calling
brain (app/llm.py) handles the message. Otherwise — or if that call fails — a
deterministic offline path (parser -> tools -> warm reply) keeps the app fully
working. Both paths call the SAME tools, so the ledger behaviour is identical.
"""
from __future__ import annotations

from app import config, db, llm, rag, tools
from app import parser as _parser


def respond(message: str, lang: str = "auto", conn=None) -> str:
    if not (message or "").strip():
        return "Namaste! 🙏 Boliye ya likhiye — jaise ‘Ramesh ko 500 udhaar likho’ ya ‘Ramesh kitna baaki hai’."

    own_conn = conn is None
    if own_conn:
        conn = db.get_connection()
        db.init_db(conn)
    try:
        if config.has_llm():
            try:
                return llm.run(message, conn, lang)
            except Exception:
                pass  # graceful fallback to the offline path
        return _offline_respond(message, conn)
    finally:
        if own_conn:
            conn.close()


def _offline_respond(message: str, conn) -> str:
    intent = _parser.parse(message)

    if intent.action == "add":
        if not intent.party:
            return "Kis ke naam likhun? Naam bataiye — jaise 'Ramesh ko 500 udhaar'."
        res = tools.add_ledger_entry(conn, intent.party, intent.txn_type, intent.amount)
        return _fmt_add(res)

    if intent.action == "balance":
        if not intent.party:
            return "Kis ka hisaab dekhna hai? Naam bataiye."
        res = tools.get_party_balance(conn, intent.party)
        if res is None:
            return f"“{intent.party}” naam ka koi khaata nahi mila."
        return _fmt_balance(res)

    if intent.action == "list":
        rows = tools.list_all_parties(conn)
        if not rows:
            return "Abhi koi khaata nahi hai. Pehla likhne ke liye boliye 'Ramesh ko 500 udhaar'."
        return _fmt_list(rows)

    # Not a ledger intent: try a knowledge-base lookup (only if a KB is loaded).
    if rag.count(conn) > 0:
        hits = rag.search(conn, message, k=1)
        if hits and hits[0]["score"] > 0.35:
            return hits[0]["text"]

    return (
        "Samajh nahi aaya 🙏. Aap likh sakte hain: "
        "‘Ramesh ko 500 udhaar likho’, ‘Suresh ne 200 jama kiye’, "
        "ya ‘Ramesh kitna baaki hai’."
    )


def _fmt_add(res: dict) -> str:
    p, amt, bal = res["party"], res["amount"], res["balance"]
    if res["type"] == "credit":
        head = f"✅ {p} ko ₹{amt:.0f} udhaar likh diya."
    else:
        head = f"✅ {p} se ₹{amt:.0f} jama kar liya."
    if bal > 0:
        tail = f"Ab {p} ke ₹{bal:.0f} baaki hain."
    elif bal == 0:
        tail = f"{p} ka hisaab ab clear hai."
    else:
        tail = f"Ab aapko {p} ko ₹{abs(bal):.0f} dene hain."
    return f"{head} {tail}"


def _fmt_balance(res: dict) -> str:
    p, bal = res["party"], res["balance"]
    if bal > 0:
        return f"{p} ke ₹{bal:.0f} baaki hain (aapko lene hain)."
    if bal == 0:
        return f"{p} ka hisaab clear hai — kuch baaki nahi."
    return f"Aapko {p} ko ₹{abs(bal):.0f} dene hain."


def _fmt_list(rows: list[dict]) -> str:
    lines = ["📒 Aapke khaate:"]
    for r in rows:
        lines.append(f"• {r['name']} ({r['type']}): ₹{r['balance']:.0f}")
    return "\n".join(lines)
