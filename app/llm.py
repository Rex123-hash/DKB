"""Groq tool-calling brain (used when GROQ_API_KEY is set).

The single LLM 'brain' receives the user message + the ledger tool schemas,
decides which tool(s) to call, we execute them against the DB, feed results
back, and it composes the final reply in the user's own language. Uses Groq's
OpenAI-compatible endpoint via `requests` (no extra SDK dependency).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import requests

from app import general, tools

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = (
    "You are Dukanbook's munshi (book-keeper) for an Indian local shopkeeper. "
    "Reply briefly and warmly in the SAME language and script the user used "
    "(Hindi, English, or Hinglish — mirror them). Currency is rupees (₹). "
    "Use the provided tools for EVERY ledger action or balance query; never "
    "invent numbers. credit = shopkeeper gave goods/lent (party owes more); "
    "debit = party paid back. After a tool runs, confirm the result in one short "
    "sentence. "
    "For ANY question about GST, income tax, invoices, bookkeeping, or business/"
    "shopkeeper advice (recovering dues, credit limits, stock, pricing, festivals, "
    "loans, licenses, payments), ALWAYS call search_knowledge first. From the "
    "returned passages, use ONLY the one(s) whose topic actually matches the "
    "question and ignore unrelated passages (the search may return some off-topic "
    "ones). Answer strictly from the matching passages. "
    "If none of the passages actually cover the question, say you are not sure — "
    "do NOT invent tax or GST rules. "
    "To set a payment/call reminder use schedule_reminder with an ISO 8601 due_at "
    "computed from the current date-time given below (e.g. 'kal' = tomorrow). "
    "For weather use get_weather, for arithmetic use calculate. For live cricket "
    "scores, say you don't have that yet."
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
            "description": "Set a payment or call reminder for a party at a given date-time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "party_name": {"type": "string"},
                    "due_at": {"type": "string", "description": "ISO 8601, e.g. 2026-06-20T10:00:00"},
                    "message": {"type": "string"},
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


def _dispatch(name: str, args: dict, conn) -> dict | list | None:
    if name == "add_ledger_entry":
        return tools.add_ledger_entry(conn, **args)
    if name == "get_party_balance":
        return tools.get_party_balance(conn, args["party_name"])
    if name == "list_all_parties":
        return tools.list_all_parties(conn)
    if name == "search_knowledge":
        return tools.search_knowledge(conn, args["query"])
    if name == "schedule_reminder":
        return tools.schedule_reminder(conn, **args)
    if name == "list_reminders":
        return tools.list_reminders(conn)
    if name == "get_weather":
        return general.get_weather(args["city"])
    if name == "calculate":
        return general.calculate(args["expression"])
    return {"error": f"unknown tool {name}"}


def _call(api_key: str, messages: list[dict]) -> dict:
    """One round-trip to Groq. Separated out so tests can monkeypatch it."""
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": os.environ.get("GROQ_MODEL", DEFAULT_MODEL),
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
            "temperature": 0.2,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def run(message: str, conn, lang: str = "auto", max_steps: int = 5) -> str:
    api_key = os.environ["GROQ_API_KEY"]
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
            return msg.get("content") or "..."
        for tc in tool_calls:
            args = json.loads(tc["function"].get("arguments") or "{}")
            result = _dispatch(tc["function"]["name"], args, conn)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str, ensure_ascii=False),
                }
            )
    return msg.get("content") or "Maaf kijiye, samajh nahi paaya."
