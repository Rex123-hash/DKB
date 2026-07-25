"""LLM tool-calling brain (local Ollama by default; Gemini or Groq if a key is set).

The single LLM 'brain' receives the user message + the ledger tool schemas,
decides which tool(s) to call, we execute them against the DB, feed results
back, and it composes the final reply in the user's own language. All providers
use the same OpenAI-compatible chat-completions format via `requests` (no extra
SDK dependency); see `_call` for how the provider is picked.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

import requests

from app import general, tools

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1/chat/completions")
GROQ_URL = os.environ.get("GROQ_URL", "https://api.groq.com/openai/v1/chat/completions")
DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_OLLAMA_MODEL = "llama3"
# Local model tried if the primary provider fails (e.g. cloud quota/outage).
FALLBACK_MODEL = os.environ.get("OLLAMA_FALLBACK_MODEL", "qwen3:8b")

SYSTEM_PROMPT = (
    "You are Dukanbook's munshi (book-keeper) for an Indian shopkeeper. "
    "Reply in ONE short, warm sentence. Mirror the user's language AND script: if "
    "they wrote in Roman/Hinglish, reply in Roman Hinglish (do NOT switch to "
    "Devanagari). Money is in rupees; write whole rupees like Rs 500, not 500.0. "
    "Always write a person's name in plain Latin spelling (Sam, Ramesh, Suresh) so "
    "the same person is one account, never in Devanagari. "
    "Use the tools for EVERY ledger action or balance query; never invent a number. "
    "Read amounts fully: '2 hazar' = 2000, '5 sau' = 500, '1 lakh' = 100000. "
    "credit = the party's udhaar goes UP, they owe MORE (words: udhaar, naam likho, "
    "'khaate/account mein daalo', add karo, diya, maal liya). "
    "debit = a payment came IN, they owe LESS (words: jama, diye, wapas, payment, "
    "vasool, paid). Plain 'daalo'/'add' without a payment word means credit. "
    "To just open an account with no amount ('X ka khaata banao'), use create_account. "
    "To set/update an existing party's phone ('X ka phone 98...'), use set_party_phone. "
    "After a credit or debit, end your reply with the new balance taken exactly from "
    "the tool result's 'balance' field. Do not mention a balance for non-ledger actions. "
    "For GST, tax, loan, licence, invoice, stock or business questions, ALWAYS call "
    "search_knowledge first and answer ONLY from the returned passages that match the "
    "question; if nothing matches, say you are not sure (never invent tax/GST rules). "
    "A question like 'what is X' / 'X kya hai' is a knowledge question; never treat a "
    "question word as a name. "
    "For a reminder or call request use schedule_reminder with an ISO 8601 due_at from "
    "the current date-time below ('kal' = tomorrow, 'Monday 10 baje' = next Monday "
    "10:00); also pass the rupee amount and a short description when mentioned. Do not "
    "read any link or URL aloud in your reply. For weather use get_weather, for maths "
    "use calculate. For live cricket scores, say you don't have that yet."
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "add_ledger_entry",
            "description": "Record a credit or debit (udhaar / payment) for a customer or supplier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "party_name": {"type": "string"},
                    "txn_type": {"type": "string", "enum": ["credit", "debit"]},
                    "amount": {"type": "number"},
                    "party_type": {"type": "string", "enum": ["customer", "supplier"]},
                },
                "required": ["party_name", "txn_type", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_account",
            "description": (
                "Create a new customer/supplier account (khaata) with no transaction. "
                "Use when the user asks to just make/open an account, e.g. "
                "'Ramesh ka khaata banao'. Optionally store a phone number."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "party_name": {"type": "string"},
                    "party_type": {"type": "string", "enum": ["customer", "supplier"]},
                    "phone": {"type": "string", "description": "10-digit Indian mobile, optional"},
                },
                "required": ["party_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_party_phone",
            "description": "Set or update the phone number of an EXISTING customer/supplier.",
            "parameters": {
                "type": "object",
                "properties": {
                    "party_name": {"type": "string"},
                    "phone": {"type": "string", "description": "10-digit Indian mobile"},
                },
                "required": ["party_name", "phone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_party_balance",
            "description": "Get how much a customer/supplier currently owes (balance).",
            "parameters": {
                "type": "object",
                "properties": {"party_name": {"type": "string"}},
                "required": ["party_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_all_parties",
            "description": "List all customers/suppliers with their balances.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "Search the accounting/GST/tax and business-advice knowledge base. "
                "Use for any question about GST, income tax, invoices, bookkeeping, "
                "or shopkeeper business advice. Returns reference passages."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_reminder",
            "description": (
                "Create a call request / payment or call reminder for a party at a "
                "given date-time. Capture the rupee amount and a short description if "
                "the user mentions them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "party_name": {"type": "string"},
                    "due_at": {"type": "string", "description": "ISO 8601, e.g. 2026-06-20T10:00:00"},
                    "amount": {"type": "number", "description": "pending rupee amount, if mentioned"},
                    "description": {"type": "string", "description": "what the call/follow-up is about"},
                    "channel": {"type": "string", "enum": ["call", "whatsapp"]},
                },
                "required": ["party_name", "due_at"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List pending reminders.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Evaluate a basic arithmetic expression.",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        },
    },
]


def _dispatch(name: str, args: dict, conn, created_sink: list | None = None) -> dict | list | None:
    if name == "create_account":
        res = tools.create_accounts(
            conn, [args["party_name"]], args.get("party_type", "customer")
        )
        phone = args.get("phone")
        if phone and res["created"]:
            res["phone"] = tools.set_phone(conn, args["party_name"], phone)
        elif res["created"] and created_sink is not None:
            # New account, no phone yet — let the brain run the phone sub-dialog.
            created_sink.extend(res["created"])
        return res
    if name == "set_party_phone":
        return tools.set_phone(conn, args["party_name"], args["phone"])
    if name == "add_ledger_entry":
        return tools.add_ledger_entry(conn, **args)
    if name == "get_party_balance":
        return tools.get_party_balance(conn, args["party_name"])
    if name == "list_all_parties":
        return tools.list_all_parties(conn)
    if name == "search_knowledge":
        return tools.search_knowledge(conn, args["query"])
    if name == "schedule_reminder":
        return tools.schedule_reminder(
            conn,
            party_name=args["party_name"],
            due_at=args["due_at"],
            message=args.get("description") or args.get("message"),
            amount=args.get("amount"),
            channel=args.get("channel", "call"),
        )
    if name == "list_reminders":
        return tools.list_reminders(conn)
    if name == "get_weather":
        return general.get_weather(args["city"])
    if name == "calculate":
        return general.calculate(args["expression"])
    return {"error": f"unknown tool {name}"}


def _providers() -> list[tuple]:
    """Ordered (url, model, headers, timeout) list to try, in preference order:
      1. Ollama OLLAMA_MODEL        - the chosen model (no key)
      2. Ollama OLLAMA_FALLBACK_MODEL - a second local model, e.g. when the
         first is a quota-limited hosted Ollama model
      3. Gemini                     - when GEMINI_API_KEY is set
      4. Groq                       - when GROQ_API_KEY is set
    Every configured provider is chained, so a rate-limited or failing one hands
    off to the next instead of dropping to the offline parser. Ollama is kept
    ahead of the cloud so a machine running it stays local. Set
    OLLAMA_FALLBACK_MODEL empty in a deployment where no Ollama is running.
    """
    json_hdr = {"Content-Type": "application/json"}
    ollama_model = os.environ.get("OLLAMA_MODEL")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    groq_key = os.environ.get("GROQ_API_KEY")
    chain: list[tuple] = []
    if ollama_model:
        chain.append((OLLAMA_URL, ollama_model, dict(json_hdr), 180))
        # Straight after the primary, before any cloud provider: if the chosen
        # model is out of quota the other local model should answer, not Gemini.
        if FALLBACK_MODEL and FALLBACK_MODEL != ollama_model:
            chain.append((OLLAMA_URL, FALLBACK_MODEL, dict(json_hdr), 180))
    if gemini_key:
        chain.append((GEMINI_URL, os.environ.get("GEMINI_MODEL", DEFAULT_MODEL),
                      {"Authorization": f"Bearer {gemini_key}", **json_hdr}, 30))
    if groq_key:
        chain.append((GROQ_URL, os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL),
                      {"Authorization": f"Bearer {groq_key}", **json_hdr}, 30))
    if not chain and FALLBACK_MODEL:  # no primary and no keys: still try local
        chain.append((OLLAMA_URL, FALLBACK_MODEL, dict(json_hdr), 180))
    if not chain:  # nothing configured at all
        chain.append((OLLAMA_URL, DEFAULT_OLLAMA_MODEL, dict(json_hdr), 180))
    return chain


def _call(api_key: str, messages: list[dict]) -> dict:
    """One LLM round-trip (OpenAI-compatible), trying each provider in turn until
    one succeeds. Monkeypatched in tests. (api_key is kept for that signature; the
    live key/URL/model come from `_providers`.)"""
    body = {"messages": messages, "tools": TOOL_SCHEMAS, "tool_choice": "auto",
            "temperature": 0.2}
    last_err: Exception | None = None
    for url, model, headers, timeout in _providers():
        try:
            resp = requests.post(url, headers=headers, json={"model": model, **body},
                                 timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):  # 200 + {"error": ...}
                raise RuntimeError(str(data["error"]))
            return data
        except Exception as e:  # try the next provider (e.g. local fallback)
            last_err = e
    raise last_err if last_err else RuntimeError("no LLM provider available")


def _clean(text: str) -> str:
    """Strip reasoning tags some local models (e.g. qwen3) add to their reply."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def run(message: str, conn, lang: str = "auto", max_steps: int = 5,
        created_sink: list | None = None) -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY") or "local"
    now = datetime.now().strftime("%A, %Y-%m-%d %H:%M")
    messages: list[dict] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\nCurrent date-time: {now}."},
        {"role": "user", "content": message},
    ]
    msg: dict = {}
    for _ in range(max_steps):
        data = _call(api_key, messages)
        msg = data["choices"][0]["message"]
        messages.append(msg)
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return _clean(msg.get("content")) or "..."
        for tc in tool_calls:
            args = json.loads(tc["function"].get("arguments") or "{}")
            result = _dispatch(tc["function"]["name"], args, conn, created_sink)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str, ensure_ascii=False),
                }
            )
    return _clean(msg.get("content")) or "Maaf kijiye, samajh nahi paaya."
