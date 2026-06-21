"""Deterministic Hinglish/Hindi/English intent parser.

This is the OFFLINE fallback path used when no Groq key is configured (and as a
safety net if the LLM call fails). It is intentionally rule-based and best-effort
— it covers the common ledger phrasings so the app is demoable without any API.
The Groq tool-calling brain handles the long tail of natural language.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Word lists (lowercased). Order of checks matters; see parse().
BALANCE_WORDS = ["baaki", "baki", "balance", "kitna dena", "kitna lena",
                 "how much", "hisaab", "hisab", "khata", "owe"]
LIST_WORDS = ["list", "saare", "sare", "sabhi", "all parties", "all accounts",
              "show parties", "customers list"]
DEBIT_WORDS = ["jama", "wapas", "wapis", "chukaya", "chukaye", "paid", "payment",
               "received", "vasool", "vasooli", "debit", "laut", "return"]
CREDIT_WORDS = ["udhaar", "udhar", "credit", "likho", "likh", "add", "lena", "diya", "diye"]

# Tokens that are never a party name.
_STOP = set(
    BALANCE_WORDS + LIST_WORDS + DEBIT_WORDS + CREDIT_WORDS
    + ["ko", "ne", "se", "ka", "ki", "ke", "hai", "hain", "ho", "kar", "karo",
       "kiye", "kiya", "rupees", "rupaye", "rupay", "rs", "rupee", "to", "for",
       "of", "me", "mein", "batao", "bata", "dikhao", "show", "kitna", "kitne",
       "namaste", "hello", "hi", "the", "a", "an"]
)

_POSTPOSITIONS = ("ko", "ne", "se", "ka", "ki", "ke")


@dataclass
class Intent:
    action: str               # 'add' | 'balance' | 'list' | 'unknown'
    party: str | None = None
    txn_type: str | None = None   # 'credit' | 'debit'
    amount: float | None = None


def _has(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _extract_amount(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


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


def parse(message: str) -> Intent:
    text = (message or "").strip().lower()
    if not text:
        return Intent("unknown")

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
