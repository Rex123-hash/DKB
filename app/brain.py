"""The brain: turn a user message into a reply.

Phase 1: real ledger handling. If GROQ_API_KEY is set, the Groq tool-calling
brain (app/llm.py) handles the message. Otherwise — or if that call fails — a
deterministic offline path (parser -> tools -> warm reply) keeps the app fully
working. Both paths call the SAME tools, so the ledger behaviour is identical.
"""
from __future__ import annotations

import re
from datetime import datetime

from app import config, db, llm, rag, tools
from app import parser as _parser

# --- general / small-talk replies (handled before the ledger or knowledge logic) ---
_GREET = {"hi", "hii", "hiii", "hello", "helo", "hlo", "hey", "heyy", "namaste",
          "namaskar", "namastey", "namaskaar", "salaam", "salam", "yo", "hola"}
_THANKS = {"thanks", "thank", "thanku", "thankyou", "thx", "shukriya", "shukria",
           "dhanyawad", "dhanyavad"}
_BYE = {"bye", "byee", "goodbye", "alvida", "tata"}
_OK = {"ok", "okay", "okie", "theek", "thik", "acha", "achha", "hmm", "hmmm"}

_REPLY_GREET = (
    "Namaste! Main aapki dukaan ka hisaab rakhne mein madad karta hoon. "
    "Aap bol ya likh sakte hain — jaise ‘Ramesh ko 500 udhaar likho’."
)
_REPLY_THANKS = "Koi baat nahi! Aur kuch likhna ho to bataiye."
_REPLY_BYE = "Theek hai, phir milte hain!"
_REPLY_OK = "Bataiye, kya likhna hai?"
_REPLY_HOWRU = "Main bilkul theek hoon, shukriya! Bataiye, aaj kaunsa hisaab likhna hai?"
_REPLY_WHO = (
    "Main Dukanbook ka AI munshi hoon. Aapka khaata (udhaar aur jama), balance, reminders, "
    "aur GST/tax/business sawaalon mein madad karta hoon — Hindi, English ya Hinglish mein."
)
_REPLY_CAP = (
    "Main ye sab kar sakta hoon:\n"
    "• Khaata likhna — ‘Ramesh ko 500 udhaar likho’, ‘Suresh ne 200 jama kiye’\n"
    "• Balance dekhna — ‘Ramesh kitna baaki hai’\n"
    "• Saare khaate dikhana — ‘saare customers dikhao’\n"
    "• Reminder lagana — ‘Ramesh ko kal payment ke liye yaad dilana’\n"
    "• GST, tax, loan, licence aur business sawaal — ‘GST kab register karna padta hai?’"
)


def _smalltalk(message: str) -> str | None:
    """Return a canned reply for greetings/small-talk, else None."""
    t = re.sub(r"[^\w\s]", " ", message.strip().lower())
    words = t.split()
    if not words:
        return None
    w = set(words)
    short = len(words) <= 4
    if any(p in t for p in ("what can you do", "kya kar sakte", "kya karte ho",
                            "tum kya kar", "aap kya kar", "help karo", "madad chahiye")):
        return _REPLY_CAP
    if any(p in t for p in ("who are you", "kaun ho", "tum kaun", "aap kaun",
                            "tumhara naam", "your name", "what are you")):
        return _REPLY_WHO
    if any(p in t for p in ("how are you", "kaise ho", "kaise hain", "kya haal",
                            "kya hal", "how r u")):
        return _REPLY_HOWRU
    if short and (w & _GREET or "good morning" in t or "good evening" in t
                  or "good afternoon" in t or "ram ram" in t):
        return _REPLY_GREET
    if (w & _THANKS) or "thank you" in t:
        return _REPLY_THANKS
    if short and ((w & _BYE) or "good night" in t or "good bye" in t):
        return _REPLY_BYE
    if short and (w & _OK):
        return _REPLY_OK
    return None


# --- multi-turn conversation state (account-creation phone capture) ---
# Keyed by session_id. Holds at most one pending action per session:
#   {"awaiting": "phone", "queue": [{"id","name"}, ...], "retried": bool}
# In-memory only — fine for a single-shopkeeper prototype; lost on restart.
_SESSIONS: dict[str, dict] = {}
_SESSION_CONTEXT: dict[str, dict] = {}

_PREVIOUS_MESSAGE_PHRASES = (
    "upar", "upar wale", "upar waale", "above", "previous message",
    "last message", "pehle bataya", "pehle diya", "already diya",
)


_SKIP_WORDS = {"skip", "chhodo", "chhod", "chhoddo", "baad", "rehne", "nahi",
               "nahin", "no", "aage", "na"}

# Guided call-reminder ("maango") dialog: name -> [number] -> purpose -> time.
# Roman 'maang' covers maango/maangna/maangne; Devanagari मांग/माँग covers voice input.
_COLLECT_WORDS = ("maang", "मांग", "माँग", "paise lene", "payment maang")
_REMINDER_STEPS = (
    "reminder_phone", "reminder_purpose", "reminder_time", "reminder_party_only",
    "reminder_amount_only", "reminder_date_only", "reminder_time_only",
    "reminder_phone_only", "reminder_collect_amount"
)
_CANCEL_PHRASES = {"cancel", "rehne do", "chhod do", "band karo", "ruko", "cancel karo"}


def _is_collect(text: str) -> bool:
    """True for a 'go collect money' request that starts the guided reminder flow."""
    return any(w in text for w in _COLLECT_WORDS)


def respond(message: str, lang: str = "auto", conn=None, session_id: str = "default") -> str:
    if not (message or "").strip():
        return "Namaste! Boliye ya likhiye — jaise ‘Ramesh ko 500 udhaar likho’."

    own_conn = conn is None
    if own_conn:
        conn = db.get_connection()
        db.init_db(conn)
    try:
        # Remember only explicit contact details for this browser session.
        explicit_phone = _parser._extract_phone(message)
        context = _SESSION_CONTEXT.get(session_id)
        if explicit_phone and context is None:
            context = {"turn": 0, "phone": None, "phone_turn": -99}
            _SESSION_CONTEXT[session_id] = context
        if context is not None:
            context["turn"] += 1
            if explicit_phone:
                context["phone"] = explicit_phone
                context["phone_turn"] = context["turn"]

        # 1. Are we mid sub-dialog for this session? Handle it first so a bare
        #    number / 'skip' / free-text answer is treated as the reply, not a
        #    new command.
        state = _SESSIONS.get(session_id)
        if state:
            awaiting = state.get("awaiting")
            if awaiting == "phone":
                handled = _handle_phone_capture(state, message, conn, session_id)
                if handled is not None:
                    return handled
                # else: escape hatch fired (state cleared) — process normally
            elif awaiting in _REMINDER_STEPS:
                return _handle_reminder_dialog(state, message, conn, session_id)

        smalltalk = _smalltalk(message)
        if smalltalk is not None:
            return smalltalk

        # 2. Guided "maango" call-reminder flow — deterministic so it works with
        #    or without an LLM, and never mis-recorded as a ledger entry.
        if _is_collect(message):
            return _start_collect(message, conn, session_id)

        # 3. Account creation is handled deterministically (works with or
        #    without an LLM key), then hands off to the phone sub-dialog.
        intent = _parser.parse(message)
        if intent.action == "create":
            return _start_create(intent, conn, session_id)
        if intent.action == "set_phone":
            return _handle_set_phone(intent, conn)
        if intent.action == "reminder":
            return _start_reminder(intent, conn, session_id)

        # 3. Everything else: LLM brain if a key is set, else the offline path.
        if config.has_llm():
            try:
                created: list[dict] = []
                pending_reminders: list[dict] = []
                reply = llm.run(
                    message, conn, lang, created_sink=created,
                    reminder_sink=pending_reminders,
                )
                # If the LLM opened account(s) (e.g. a phrasing the parser missed,
                # like a typo'd "craete"), take over with our phone sub-dialog so
                # the follow-up is consistent with the deterministic path.
                if created:
                    return _begin_phone_capture(created, session_id)
                if pending_reminders:
                    pending = pending_reminders[0]
                    pending["provided_phone"] = pending.get("provided_phone") or explicit_phone
                    pending["date_provided"] = _parser._extract_date_part(message.lower()) is not None
                    pending["time_provided"] = _parser._extract_time_part(message.lower()) is not None
                    return _begin_reminder_details(pending, session_id)
                return reply
            except Exception:
                pass  # graceful fallback to the offline path
        return _offline_respond(message, conn)
    finally:
        if own_conn:
            conn.close()


def _ask_phone(name: str, nxt: bool = False) -> str:
    lead = "Ab " if nxt else ""
    return f"{lead}{name} ka phone number bataiye? (ya ‘skip’ boliye)"


def _start_create(intent, conn, session_id: str) -> str:
    names = intent.names or []
    if not names:
        return "Kis ka khaata banaun? Naam bataiye — jaise ‘Ramesh ka khaata banao’."

    res = tools.create_accounts(conn, names, intent.party_type)
    created, existing = res["created"], res["existing"]

    if not created:
        joined = ", ".join(existing)
        return f"“{joined}” ka khaata to pehle se hai."

    queue = list(created)
    extra = ""
    # An inline phone applies to the first created account.
    if intent.phone:
        first = queue.pop(0)
        r = tools.set_phone(conn, first["name"], intent.phone)
        if "error" in r:
            queue.insert(0, first)  # invalid -> ask for it normally
        else:
            extra = f" {first['name']} ka number bhi save kar liya."

    if not queue:  # everything had an inline phone already
        _SESSIONS.pop(session_id, None)
        return _fmt_created(created, existing) + extra
    _SESSIONS[session_id] = {"awaiting": "phone", "queue": queue, "retried": False}
    return _fmt_created(created, existing) + extra + " " + _ask_phone(queue[0]["name"])


def _handle_set_phone(intent, conn) -> str:
    """Set/update the phone for an existing party (works with or without the LLM)."""
    if not intent.party:
        return "Kis ka phone number save karun? Naam bataiye — jaise ‘Ramesh ka phone 9876543210’."
    res = tools.set_phone(conn, intent.party, intent.phone or "")
    if res.get("error") == "not_found":
        return f"“{intent.party}” naam ka koi khaata nahi mila. Pehle khaata banaiye."
    if res.get("error") == "invalid_phone":
        return f"Yeh number theek nahi laga. {intent.party} ka 10-digit mobile number bataiye."
    return f"Ho gaya! {res['party']} ka phone number save ho gaya: {res['phone']}."


def _begin_phone_capture(created: list[dict], session_id: str) -> str:
    """Start the phone sub-dialog for accounts the LLM just created."""
    _SESSIONS[session_id] = {"awaiting": "phone", "queue": list(created), "retried": False}
    return _fmt_created(created, []) + " " + _ask_phone(created[0]["name"])


def _fmt_created(created: list[dict], existing: list[str]) -> str:
    names = ", ".join(c["name"] for c in created)
    n = len(created)
    head = (f"Naya khaata ban gaya: {names}." if n == 1
            else f"{n} naye khaate ban gaye: {names}.")
    if existing:
        head += f" ({', '.join(existing)} pehle se the.)"
    return head


def _advance(state: dict, session_id: str, prefix: str) -> str:
    """Drop the current name and prompt for the next, or finish."""
    state["queue"].pop(0)
    state["retried"] = False
    if state["queue"]:
        return f"{prefix} {_ask_phone(state['queue'][0]['name'], nxt=True)}"
    _SESSIONS.pop(session_id, None)
    return f"{prefix} Ho gaya! Saare khaate taiyaar hain."


def _handle_phone_capture(state: dict, message: str, conn, session_id: str) -> str | None:
    """Interpret a reply during phone capture. Returns a reply string, or None
    to signal the escape hatch (state cleared; caller processes normally)."""
    current = state["queue"][0]
    phone = tools.normalize_phone(message)
    if phone:
        tools.set_phone(conn, current["name"], message)
        return _advance(state, session_id, f"{current['name']} ka number save ho gaya.")

    tokens = re.sub(r"[^\w\s]", " ", message.lower()).split()
    if any(t in _SKIP_WORDS for t in tokens):
        return _advance(state, session_id, f"Theek hai, {current['name']} ko abhi chhod diya.")

    digits = re.sub(r"\D", "", message)
    letters = re.sub(r"[^A-Za-zऀ-ॿ]", "", message)
    # A digit-heavy reply with almost no letters is a bad number attempt, not a
    # command — re-ask once, then move on. (Checked before the command escape so
    # a bare invalid number isn't mistaken for an amount/ledger command.)
    if len(digits) >= 5 and len(letters) <= 2:
        if not state.get("retried"):
            state["retried"] = True
            return (f"Yeh number theek nahi laga. {current['name']} ka 10-digit "
                    "mobile number dobara bataiye (ya ‘skip’).")
        return _advance(state, session_id, f"{current['name']} ka number samajh nahi aaya, abhi chhod diya.")

    # A clear command (ledger/balance/list/create) cancels capture — never trap.
    if _parser.parse(message).action != "unknown":
        _SESSIONS.pop(session_id, None)
        return None

    # Anything else: don't trap the user — abandon capture and process normally.
    _SESSIONS.pop(session_id, None)
    return None


def _ask_purpose(r: dict) -> str:
    """Ask the reason for the call, confirming the number when we have it."""
    amt = f"₹{r['amount']:.0f} " if r.get("amount") else ""
    num = f" jinka number {r['phone']} hai" if r.get("phone") else ""
    return f"{r['name']}{num}, unse {amt}maangne ka purpose kya hai?"


def _begin_reminder_details(reminder: dict, session_id: str) -> str:
    """Pause a reminder and ask for the first mandatory detail that is missing."""
    pending = {
        "name": reminder.get("party") or reminder.get("name"),
        "due_at": reminder.get("due_at"),
        "purpose": reminder.get("message"),
        "amount": reminder.get("amount"),
        "channel": reminder.get("channel", "call"),
        "provided_phone": reminder.get("provided_phone"),
        "date_provided": bool(reminder.get("date_provided")),
        "time_provided": bool(reminder.get("time_provided")),
    }
    if reminder.get("needs_party") or not tools.valid_party_name(pending["name"]):
        awaiting = "reminder_party_only"
        question = "Please specify: reminder kiske liye lagana hai? Vyakti ya business ka naam bataiye."
    elif reminder.get("needs_amount") or not pending.get("amount"):
        awaiting = "reminder_amount_only"
        question = f"{pending['name']} ke reminder ki amount kitni hai? Rupees mein bataiye."
    elif reminder.get("needs_date") or not pending["date_provided"]:
        awaiting = "reminder_date_only"
        question = "Reminder kis date ko chahiye? Jaise ‘aaj’, ‘kal’ ya ‘5 August’."
    elif reminder.get("needs_time") or not pending["time_provided"]:
        awaiting = "reminder_time_only"
        question = "Reminder kis time par chahiye? Jaise ‘shaam 5 baje’ ya ‘5 PM’."
    else:
        awaiting = "reminder_phone_only"
        question = (
            f"{pending['name']} ka 10-digit mobile number bataiye. Phone ke bina reminder "
            "save nahi hoga; bina number ke rakhna ho to ‘skip’ boliye."
        )
    _SESSIONS[session_id] = {"awaiting": awaiting, "reminder": pending}
    return question


def _continue_reminder(r: dict, conn, session_id: str, *, skip_phone: bool = False) -> str:
    if not tools.valid_party_name(r.get("name")):
        return _begin_reminder_details({**r, "party": None, "needs_party": True}, session_id)
    if not r.get("amount") or r["amount"] <= 0:
        return _begin_reminder_details({**r, "party": r.get("name"), "needs_amount": True}, session_id)
    if not r.get("date_provided"):
        return _begin_reminder_details({**r, "party": r.get("name"), "needs_date": True}, session_id)
    if not r.get("time_provided"):
        return _begin_reminder_details({**r, "party": r.get("name"), "needs_time": True}, session_id)
    result = tools.schedule_reminder(
        conn,
        r.get("name"),
        r.get("due_at"),
        message=r.get("purpose"),
        amount=r.get("amount"),
        channel=r.get("channel", "call"),
        skip_phone=skip_phone,
    )
    provided_phone = tools.normalize_phone(r.get("provided_phone") or "")
    if result.get("needs_phone") and provided_phone:
        tools.set_phone(conn, r["name"], provided_phone)
        result = tools.schedule_reminder(
            conn,
            r["name"],
            r.get("due_at"),
            message=r.get("purpose"),
            amount=r.get("amount"),
            channel=r.get("channel", "call"),
        )
    if any(result.get(key) for key in ("needs_party", "needs_amount", "needs_phone")):
        result["provided_phone"] = r.get("provided_phone")
        result["date_provided"] = r.get("date_provided")
        result["time_provided"] = r.get("time_provided")
        return _begin_reminder_details(result, session_id)
    _SESSIONS.pop(session_id, None)
    _SESSION_CONTEXT.pop(session_id, None)
    return _fmt_reminder(result)


def _start_reminder(intent, conn, session_id: str) -> str:
    """Create an ordinary reminder only after all mandatory details are present."""
    pending = {
        "party": intent.party if tools.valid_party_name(intent.party) else None,
        "due_at": intent.due_at,
        "message": intent.message,
        "amount": intent.amount,
        "channel": "call",
        "provided_phone": intent.phone,
        "date_provided": intent.reminder_date_provided,
        "time_provided": intent.reminder_time_provided,
    }
    if pending["party"] is None:
        pending["needs_party"] = True
        return _begin_reminder_details(pending, session_id)
    internal = {
        "name": pending["party"], "due_at": pending["due_at"],
        "purpose": pending["message"], "amount": pending["amount"],
        "channel": pending["channel"], "provided_phone": pending["provided_phone"],
        "date_provided": pending["date_provided"],
        "time_provided": pending["time_provided"],
    }
    return _continue_reminder(internal, conn, session_id)


def _update_reminder_due(r: dict, message: str, *, allow_bare_time: bool = False) -> tuple[bool, bool]:
    """Merge explicitly supplied date/time parts into a pending reminder."""
    text = message.strip().lower()
    due_date = _parser._extract_date_part(text)
    due_time = _parser._extract_time_part(text)
    if due_time is None and allow_bare_time:
        bare = re.sub(r"[^\w]", "", text)
        if bare.isdigit() and 0 <= int(bare) <= 23:
            due_time = datetime.min.replace(hour=int(bare)).time()
        else:
            for word, value in _parser._NUM_UNITS.items():
                if bare == word and value <= 12:
                    due_time = datetime.min.replace(hour=value).time()
                    break
    try:
        current = datetime.fromisoformat(r.get("due_at") or "")
    except ValueError:
        current = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
    if due_date:
        r["date_provided"] = True
    if due_time:
        r["time_provided"] = True
    r["due_at"] = datetime.combine(
        due_date or current.date(), due_time or current.time().replace(second=0, microsecond=0)
    ).isoformat(timespec="minutes")
    return due_date is not None, due_time is not None


def _start_collect(message: str, conn, session_id: str) -> str:
    """Begin the guided call-reminder dialog from 'Rahul se 500 maango'."""
    name = _parser._extract_party(message)
    # A 10-digit phone must never be read as the rupee amount.
    amount = _parser._extract_amount(re.sub(r"\d{10,}", " ", message))
    if not tools.valid_party_name(name):
        return "Kis se paise maangne hain? Naam bataiye — jaise ‘Rahul se 500 maango’."

    row = db.find_party_by_name(conn, name)
    if row is None:  # unknown customer — create the account so we can attach the reminder
        pid = db.add_party(conn, name, "customer")
        row = db.get_party(conn, pid)

    inline_phone = _parser._extract_phone(message)
    if inline_phone:
        tools.set_phone(conn, row["name"], inline_phone)
        row = db.find_party_by_name(conn, row["name"])

    r = {"name": row["name"], "amount": amount, "phone": row["phone"], "purpose": None}
    if amount is None or amount <= 0:
        _SESSIONS[session_id] = {"awaiting": "reminder_collect_amount", "reminder": r}
        return f"{row['name']} se kitni amount leni hai? Rupees mein bataiye."
    if row["phone"]:
        _SESSIONS[session_id] = {"awaiting": "reminder_purpose", "reminder": r}
        return _ask_purpose(r)
    _SESSIONS[session_id] = {"awaiting": "reminder_phone", "reminder": r}
    return f"{row['name']} ka number kya hai? (ya ‘skip’ boliye)"


def _handle_reminder_dialog(state: dict, message: str, conn, session_id: str) -> str:
    """Advance the guided reminder dialog: number -> purpose -> time -> save."""
    r = state["reminder"]
    inline_phone = _parser._extract_phone(message)
    if inline_phone:
        r["provided_phone"] = inline_phone

    if message.strip().lower() in _CANCEL_PHRASES:
        _SESSIONS.pop(session_id, None)
        _SESSION_CONTEXT.pop(session_id, None)
        return "Theek hai, reminder cancel kar diya."

    if state["awaiting"] == "reminder_party_only":
        candidate = _parser._extract_party(message)
        if not tools.valid_party_name(candidate):
            return "Please specify kiske liye reminder hai — vyakti ya business ka actual naam bataiye."
        r["name"] = candidate.strip()
        inline_phone = _parser._extract_phone(message)
        if inline_phone:
            r["provided_phone"] = inline_phone
        amount_text = re.sub(r"\d{10,}", " ", message)
        inline_amount = _parser._extract_amount(amount_text.lower())
        if inline_amount and inline_amount > 0:
            r["amount"] = inline_amount
        _update_reminder_due(r, message)
        return _continue_reminder(r, conn, session_id)

    if state["awaiting"] == "reminder_amount_only":
        inline_phone = _parser._extract_phone(message)
        if inline_phone:
            r["provided_phone"] = inline_phone
        amount = _parser._extract_amount(re.sub(r"\d{10,}", " ", message).lower())
        if amount is None or amount <= 0:
            return f"{r['name']} ke reminder ki positive amount rupees mein bataiye — jaise ‘500’."
        r["amount"] = amount
        _update_reminder_due(r, message)
        return _continue_reminder(r, conn, session_id)

    if state["awaiting"] == "reminder_date_only":
        got_date, _ = _update_reminder_due(r, message)
        if not got_date:
            return "Date samajh nahi aayi. ‘Aaj’, ‘kal’, ‘5 August’ ya ‘05/08/2026’ boliye."
        return _continue_reminder(r, conn, session_id)

    if state["awaiting"] == "reminder_time_only":
        _, got_time = _update_reminder_due(r, message, allow_bare_time=True)
        if not got_time:
            return "Time samajh nahi aaya. ‘Shaam 5 baje’, ‘5 PM’ ya ‘17:00’ boliye."
        return _continue_reminder(r, conn, session_id)

    if state["awaiting"] == "reminder_collect_amount":
        amount = _parser._extract_amount(message.lower())
        if amount is None or amount <= 0:
            return f"{r['name']} se leni wali positive amount bataiye — jaise ‘500’."
        r["amount"] = amount
        if r.get("phone"):
            state["awaiting"] = "reminder_purpose"
            return _ask_purpose(r)
        state["awaiting"] = "reminder_phone"
        return f"{r['name']} ka number kya hai? (ya ‘skip’ boliye)"

    if state["awaiting"] == "reminder_phone":
        phone = tools.normalize_phone(message) or _phone_from_previous_message(message, session_id)
        if phone:
            tools.set_phone(conn, r["name"], phone)
            r["phone"] = phone
            state["awaiting"] = "reminder_purpose"
            return _ask_purpose(r)
        tokens = re.sub(r"[^\w\s]", " ", message.lower()).split()
        if "skip" in tokens:
            r["phone_skipped"] = True
            state["awaiting"] = "reminder_purpose"
            return _ask_purpose(r)
        if _references_previous_message(message):
            return "Maine recent messages check kiye, lekin valid 10-digit number nahi mila. Number dobara bataiye ya ‘skip’ boliye."
        return f"Yeh number theek nahi laga. {r['name']} ka 10-digit mobile bataiye (ya ‘skip’)."

    if state["awaiting"] == "reminder_phone_only":
        phone = tools.normalize_phone(message) or _phone_from_previous_message(message, session_id)
        tokens = re.sub(r"[^\w\s]", " ", message.lower()).split()
        skipped = "skip" in tokens
        if not phone and not skipped:
            if _references_previous_message(message):
                return "Maine recent messages check kiye, lekin valid 10-digit number nahi mila. Number dobara bataiye ya ‘skip’ boliye."

            return (
                f"Yeh number theek nahi laga. {r['name']} ka 10-digit mobile bataiye, "
                "ya bina number ke reminder rakhne ke liye ‘skip’ boliye."
            )
        if phone:
            tools.set_phone(conn, r["name"], phone)
            r["provided_phone"] = phone
        return _continue_reminder(r, conn, session_id, skip_phone=skipped)

    if state["awaiting"] == "reminder_purpose":
        r["purpose"] = message.strip()
        state["awaiting"] = "reminder_time"
        return "Kis samay pe call karwana chahenge? (jaise ‘kal shaam 5 baje’)"

    # reminder_time: parse the time, save the call reminder, finish.
    due = _parser._extract_due(message.lower())
    tools.schedule_reminder(conn, r["name"], due, message=r.get("purpose"),
                            amount=r.get("amount"), channel="call",
                            skip_phone=bool(r.get("phone_skipped")))
    _SESSIONS.pop(session_id, None)
    _SESSION_CONTEXT.pop(session_id, None)
    when = _humanize_due(due)
    return (f"Theek hai, {r['name']} ko {when} par call kar diya jayega. "
            f"Aapka jawab Reminders section me dikh jayega. Dhanyawaad.")


def _references_previous_message(message: str) -> bool:
    text = message.strip().lower()
    return any(phrase in text for phrase in _PREVIOUS_MESSAGE_PHRASES)


def _phone_from_previous_message(message: str, session_id: str) -> str | None:
    """Resolve an explicitly referenced recent phone; never invent one."""
    text = message.strip().lower()
    if not any(phrase in text for phrase in _PREVIOUS_MESSAGE_PHRASES):
        return None
    context = _SESSION_CONTEXT.get(session_id) or {}
    if context.get("turn", 0) - context.get("phone_turn", -99) > 6:
        return None
    return tools.normalize_phone(context.get("phone") or "")


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

    if intent.action == "reminder":
        if not intent.party:
            return "Kis ke liye reminder lagaun? Naam bataiye — jaise 'Ramesh ko kal call karna'."
        res = tools.schedule_reminder(conn, intent.party, intent.due_at,
                                      message=intent.message, amount=intent.amount)
        return _fmt_reminder(res)

    # Not a ledger intent: try a knowledge-base lookup (only if a KB is loaded).
    if rag.count(conn) > 0:
        hits = rag.search(conn, message, k=3)
        answer = rag.grounded_answer(message, hits)
        if answer:
            return answer
        if hits:
            return (
                "Mujhe is sawal par apne notes mein poori pakki jankari nahi "
                f"mili. Sabse kareeb note: {hits[0]['citation']}"
            )

    return (
        "Samajh nahi aaya. Aap likh sakte hain: "
        "‘Ramesh ko 500 udhaar likho’, ‘Suresh ne 200 jama kiye’, "
        "ya ‘Ramesh kitna baaki hai’."
    )


def _fmt_add(res: dict) -> str:
    p, amt, bal = res["party"], res["amount"], res["balance"]
    if res["type"] == "credit":
        head = f"Ho gaya! {p} ko ₹{amt:.0f} udhaar likh diya."
    else:
        head = f"Ho gaya! {p} se ₹{amt:.0f} jama kar liya."
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
    lines = ["Aapke khaate:"]
    for r in rows:
        lines.append(f"• {r['name']} ({r['type']}): ₹{r['balance']:.0f}")
    return "\n".join(lines)


def _humanize_due(due_at: str) -> str:
    try:
        dt = datetime.fromisoformat(due_at)
        return dt.strftime("%d %b, %H:%M")
    except (TypeError, ValueError):
        return due_at or "jaldi"


def _fmt_reminder(res: dict) -> str:
    """Warm confirmation for a call reminder. Never reads the WhatsApp URL aloud."""
    p = res["party"]
    when = _humanize_due(res.get("due_at"))
    amt = res.get("amount")
    amt_txt = f" ₹{amt:.0f} ke payment" if amt else ""
    head = f"Ho gaya! {p} ko {when}{amt_txt} ke liye call reminder laga diya."
    if res.get("whatsapp_link"):
        head += " WhatsApp bhejne ka link Reminders page par hai."
    elif res.get("phone") is None:
        head += f" ({p} ka phone number add karein WhatsApp ke liye.)"
    return head
