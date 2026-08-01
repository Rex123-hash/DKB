"""Deterministic Hinglish/Hindi/English intent parser.

This is the OFFLINE fallback path used when no Groq key is configured (and as a
safety net if the LLM call fails). It is intentionally rule-based and best-effort
— it covers the common ledger phrasings so the app is demoable without any API.
The Groq tool-calling brain handles the long tail of natural language.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# Word lists (lowercased). Order of checks matters; see parse().
BALANCE_WORDS = ["baaki", "baki", "balance", "kitna dena", "kitna lena",
                 "how much", "hisaab", "hisab", "khata", "owe"]
LIST_WORDS = ["list", "saare", "sare", "sabhi", "all parties", "all accounts",
              "show parties", "customers list"]
DEBIT_WORDS = ["jama", "wapas", "wapis", "chukaya", "chukaye", "paid", "payment",
               "received", "vasool", "vasooli", "debit", "laut", "return"]
CREDIT_WORDS = ["udhaar", "udhar", "credit", "likho", "likh", "add", "lena", "diya", "diye"]

# Account-creation vocabulary.
ACCOUNT_WORDS = ["khaata", "khata", "khaate", "khate", "account", "accounts"]
MAKE_WORDS = ["banao", "bana", "banado", "banade", "banwao", "banwa", "kholo",
              "khol", "kholdo", "create", "new", "naya", "nayi", "naye"]
SUPPLIER_WORDS = ["supplier", "vendor", "dukaandaar", "dukandar", "distributor",
                  "thok", "wholesaler", "wholesale"]
# Call-request / reminder vocabulary (offline best-effort; the LLM is primary).
REMINDER_WORDS = ["yaad dila", "yaad dilana", "reminder", "remind", "call karna",
                  "call karo", "call kar do", "call lagana", "call laga", "call karke",
                  "follow up", "followup", "follow-up", "call back"]
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
# Words to drop when pulling names out of a create command.
_NAME_NOISE = set(ACCOUNT_WORDS + MAKE_WORDS + SUPPLIER_WORDS
                  + ["aur", "and", "&", "do", "de", "ka", "ke", "ki", "for"])

# Tokens that are never a party name.
_STOP = set(
    BALANCE_WORDS + LIST_WORDS + DEBIT_WORDS + CREDIT_WORDS
    + ["ko", "ne", "se", "ka", "ki", "ke", "hai", "hain", "ho", "kar", "karo",
       "kiye", "kiya", "rupees", "rupaye", "rupay", "rs", "rupee", "to", "for",
       "of", "me", "mein", "batao", "bata", "dikhao", "show", "kitna", "kitne",
       "namaste", "hello", "hi", "the", "a", "an",
       # reminder/time words — never a party name
       "call", "reminder", "remind", "yaad", "dila", "dilao", "dilana", "follow",
       "followup", "karna", "lagana", "laga", "baje", "am", "pm", "kal", "aaj",
       "parso", "tomorrow", "today", "next", "liye", "regarding", "pending",
       "payment", "back", "about",
       # phone words — never a party name
       "phone", "mobile", "contact", "number", "update", "save", "set"]
)

_POSTPOSITIONS = ("ko", "ne", "se", "ka", "ki", "ke")


@dataclass
class Intent:
    action: str               # 'add' | 'balance' | 'list' | 'create' | 'unknown'
    party: str | None = None
    txn_type: str | None = None   # 'credit' | 'debit'
    amount: float | None = None
    names: list[str] | None = None       # for 'create' (one or more)
    party_type: str = "customer"         # 'customer' | 'supplier'
    phone: str | None = None             # raw inline phone digits, if given
    due_at: str | None = None            # for 'reminder' (ISO 8601)
    reminder_date_provided: bool = False
    reminder_time_provided: bool = False
    message: str | None = None           # description (e.g. the reminder text)


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


# Spoken number words (roman + Devanagari) for when speech has no digits.
_NUM_UNITS = {
    "ek": 1, "एक": 1, "do": 2, "दो": 2, "teen": 3, "तीन": 3, "char": 4,
    "chaar": 4, "चार": 4, "paanch": 5, "panch": 5, "पांच": 5, "पाँच": 5,
    "chhe": 6, "che": 6, "छह": 6, "छे": 6, "saat": 7, "सात": 7, "aath": 8,
    "आठ": 8, "nau": 9, "नौ": 9, "das": 10, "दस": 10,
}
_PAANSO = ("paanso", "panso", "paansau", "पान्सो", "पांसो", "पाँसो")
_HAZAAR = r"(?:hazaar|hazar|हज़ार|हजार)"
_SAU = r"(?:sau|सौ)"


def _word_amount(text: str) -> float | None:
    """Best-effort spoken amount: 'paanso'/'paanch sau' -> 500, 'do hazaar' -> 2000."""
    t = text.lower()
    if any(w in t for w in _PAANSO):
        return 500.0
    for w, v in _NUM_UNITS.items():
        if re.search(w + r"\s*" + _HAZAAR, t):
            return v * 1000.0
    if re.search(r"\b" + _HAZAAR + r"\b", t):
        return 1000.0
    for w, v in _NUM_UNITS.items():
        if re.search(w + r"\s*" + _SAU, t):
            return v * 100.0
    if re.search(r"\b" + _SAU + r"\b", t):
        return 100.0
    return None


def _extract_amount(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))
    return _word_amount(text)  # fall back to spoken number words


def _extract_party(text: str) -> str | None:
    # 1) token right before a Hindi postposition: "Ramesh ko", "Suresh ne"
    m = re.search(
        r"([A-Za-zऀ-ॿ]+)\s+(?:" + "|".join(_POSTPOSITIONS) + r")\b",
        text, re.I,
    )
    if m and m.group(1).lower() not in _STOP:
        return m.group(1)
    # 2) English "to/for/of X"
    m = re.search(r"\b(?:to|for|of)\s+([A-Za-zऀ-ॿ]+)", text, re.I)
    if m and m.group(1).lower() not in _STOP:
        return m.group(1)
    # 3) fallback: first word that is not a stop-word / number
    for tok in re.findall(r"[A-Za-zऀ-ॿ]+", text):
        if tok.lower() not in _STOP:
            return tok
    return None


def _is_create(text: str) -> bool:
    """A create command names an account word AND a make/open verb."""
    return _has(text, ACCOUNT_WORDS) and _has(text, MAKE_WORDS)


PHONE_WORDS = ["phone", "mobile", "contact", "number", "no."]


def _is_set_phone(text: str) -> bool:
    """A 'set phone' command mentions a phone word and carries a 10-digit number."""
    return _has(text, PHONE_WORDS) and _extract_phone(text) is not None


def _extract_names(message: str) -> list[str]:
    """Pull party names out of a create command, preserving original casing."""
    names = []
    for tok in re.findall(r"[A-Za-zऀ-ॿ]+", message):
        low = tok.lower()
        if low in _NAME_NOISE or low in _STOP:
            continue
        names.append(tok)
    return names


def _extract_phone(text: str) -> str | None:
    """Return the raw phone digits if the command includes one, else None."""
    match = re.search(
        r"(?<!\d)(?:(?:\+?91|0)[\s-]?)?([6-9](?:[\s-]?\d){9})(?!\d)",
        text,
    )
    return re.sub(r"\D", "", match.group(1)) if match else None


def _extract_date_part(text: str, now: datetime | None = None) -> date | None:
    """Return an explicitly spoken reminder date, or None when it was omitted."""
    now = now or datetime.now()
    if "parso" in text or "परसों" in text or "परसो" in text:
        return now.date() + timedelta(days=2)
    if "kal" in text or "tomorrow" in text or "कल" in text:
        return now.date() + timedelta(days=1)
    if "aaj" in text or "today" in text or "आज" in text:
        return now.date()
    iso = re.search(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)", text)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    numeric = re.search(r"(?<!\d)(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?(?!\d)", text)
    if numeric:
        year = int(numeric.group(3) or now.year)
        year = year + 2000 if year < 100 else year
        try:
            return date(year, int(numeric.group(2)), int(numeric.group(1)))
        except ValueError:
            return None
    named = re.search(
        r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")(?:\s+(\d{4}))?",
        text,
    )
    if named:
        try:
            return date(int(named.group(3) or now.year), _MONTHS[named.group(2)], int(named.group(1)))
        except ValueError:
            return None
    for i, weekday in enumerate(_WEEKDAYS):
        if weekday in text:
            ahead = (i - now.weekday()) % 7
            return now.date() + timedelta(days=ahead or 7)
    return None


def _extract_time_part(text: str) -> time | None:
    """Return an explicitly spoken clock time, or None when it was omitted/invalid."""
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text)
    if m:
        raw_hour = int(m.group(1))
        mins = int(m.group(2) or 0)
        if 1 <= raw_hour <= 12 and 0 <= mins <= 59:
            hour = raw_hour % 12 + (12 if m.group(3) == "pm" else 0)
            return time(hour, mins)
        return None

    clock = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", text)
    if clock:
        hour, minute = int(clock.group(1)), int(clock.group(2))
        return time(hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None

    hour: int | None = None
    m = re.search(r"(\d{1,2})\s*(?:baje|बजे)", text)
    if m:
        candidate = int(m.group(1))
        if 0 <= candidate <= 23:
            hour = candidate
    else:
        # Voice transcription commonly emits "paanch baje" / "पांच बजे".
        for word, value in _NUM_UNITS.items():
            if value <= 12 and re.search(rf"(?<!\w){re.escape(word)}\s*(?:baje|बजे)", text):
                hour = value
                break
    if hour is None:
        return None
    if 1 <= hour <= 11 and _has(text, ["shaam", "sham", "evening", "raat", "night",
                                          "dopahar", "afternoon", "शाम", "रात", "दोपहर"]):
        hour += 12
    return time(hour, 0)


def _extract_due(text: str, now: datetime | None = None) -> str:
    """Best-effort date-time; legacy callers receive today/10:00 defaults."""
    now = now or datetime.now()
    due_date = _extract_date_part(text, now) or now.date()
    due_time = _extract_time_part(text) or time(10, 0)
    return datetime.combine(due_date, due_time).isoformat(timespec="minutes")


def parse(message: str) -> Intent:
    text = (message or "").strip().lower()
    if not text:
        return Intent("unknown")

    if _has(text, REMINDER_WORDS):
        # Strip time expressions and phone-length digit runs before reading the
        # money amount ("10 baje" != ₹10; a 10-digit phone != a rupee amount).
        cleaned = re.sub(r"\d{1,2}(?::\d{2})?\s*(?:baje|am|pm|o'?clock)", " ", text)
        cleaned = re.sub(r"\d{10,}", " ", cleaned)
        explicit_date = _extract_date_part(text)
        explicit_time = _extract_time_part(text)
        return Intent(
            "reminder",
            party=_extract_party(message),
            amount=_extract_amount(cleaned),
            phone=_extract_phone(text),
            due_at=_extract_due(text),
            reminder_date_provided=explicit_date is not None,
            reminder_time_provided=explicit_time is not None,
            message=message,
        )

    if _is_create(text):
        party_type = "supplier" if _has(text, SUPPLIER_WORDS) else "customer"
        return Intent(
            "create",
            names=_extract_names(message),
            party_type=party_type,
            phone=_extract_phone(text),
        )

    if _is_set_phone(text):
        return Intent("set_phone", party=_extract_party(message), phone=_extract_phone(text))

    if _has(text, BALANCE_WORDS):
        return Intent("balance", party=_extract_party(message))
    if _has(text, LIST_WORDS):
        return Intent("list")

    amount = _extract_amount(text)
    if amount is not None:
        # 'ne' + a give-verb means the party paid us back -> debit
        ne_payment = re.search(r"\bne\b.*\b(diya|diye|de diye|paid|jama)\b", text)
        txn_type = "debit" if (_has(text, DEBIT_WORDS) or ne_payment) else "credit"
        return Intent("add", party=_extract_party(message), txn_type=txn_type, amount=amount)

    return Intent("unknown")
