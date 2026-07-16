"""Deterministic Hinglish/Hindi/English intent parser.

This is the OFFLINE fallback path used when no Groq key is configured (and as a
safety net if the LLM call fails). It is intentionally rule-based and best-effort
— it covers the common ledger phrasings so the app is demoable without any API.
The Groq tool-calling brain handles the long tail of natural language.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

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
    digits = re.sub(r"\D", "", text)
    return digits if len(digits) >= 10 else None


def _extract_due(text: str, now: datetime | None = None) -> str:
    """Best-effort relative date+time from Hinglish/English. Defaults to 10:00."""
    now = now or datetime.now()
    d = now.date()
    if "parso" in text or "परसों" in text or "परसो" in text:
        d = d + timedelta(days=2)
    elif "kal" in text or "tomorrow" in text or "कल" in text:
        d = d + timedelta(days=1)
    elif "aaj" in text or "today" in text or "आज" in text:
        pass
    else:
        for i, wd in enumerate(_WEEKDAYS):
            if wd in text:
                ahead = (i - now.weekday()) % 7
                d = d + timedelta(days=ahead or 7)  # next such weekday
                break

    hour, minute = 10, 0
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(3) == "pm" else 0)
        mins = int(m.group(2) or 0)
        if 0 <= h <= 23 and 0 <= mins <= 59:  # ignore garbage like "4:99 pm"
            hour, minute = h, mins
    else:
        m = re.search(r"(\d{1,2})\s*(?:baje|बजे)", text)
        if m:
            h = int(m.group(1))
            if 0 <= h <= 23:  # ignore "50 baje" (mis-transcription) -> keep 10:00
                hour = h
                # "shaam/raat/dopahar 5 baje" is PM; "subah 5 baje" stays AM.
                if 1 <= h <= 11 and _has(text, ["shaam", "sham", "evening", "raat",
                                                "night", "dopahar", "afternoon",
                                                "शाम", "रात", "दोपहर"]):
                    hour = h + 12
    return datetime.combine(d, time(hour, minute)).isoformat(timespec="minutes")


def parse(message: str) -> Intent:
    text = (message or "").strip().lower()
    if not text:
        return Intent("unknown")

    if _has(text, REMINDER_WORDS):
        # Strip time expressions and phone-length digit runs before reading the
        # money amount ("10 baje" != ₹10; a 10-digit phone != a rupee amount).
        cleaned = re.sub(r"\d{1,2}(?::\d{2})?\s*(?:baje|am|pm|o'?clock)", " ", text)
        cleaned = re.sub(r"\d{10,}", " ", cleaned)
        return Intent(
            "reminder",
            party=_extract_party(message),
            amount=_extract_amount(cleaned),
            due_at=_extract_due(text),
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
