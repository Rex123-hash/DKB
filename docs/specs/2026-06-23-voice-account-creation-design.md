# Voice/Text Account Creation with Phone Capture — Design

Date: 2026-06-23
Status: Approved (ready for implementation plan)
Feature phase: Phase 4 (builds on Phase 0–3: skeleton, ledger tool-calling, RAG/voice)

## 1. Problem

Today an account (party) is created only as a *side effect* of the first
transaction, via `get_or_create_party` in `app/tools.py`. There is no way to:

- Create an account by itself, conversationally — "Ramesh ka khaata banao".
- Create several accounts in one command — "Ramesh aur Suresh aur Mukesh ka khaata banao".
- Capture a phone number for a party through the assistant. The `party.phone`
  column exists in the schema but nothing in the conversational path ever writes it.

The Khata (Ledger) screen already has a form-based "Naya khaata" tab with a phone
field. This feature is **only** about the conversational / voice path in the AI
Assistant. The Khata form is out of scope and will not be changed.

## 2. Goals

1. Create one or more accounts from a single voice/text command (Hindi / English / Hinglish).
2. After creation, prompt for each new account's phone number, one at a time, skippable.
3. Capture the phone the user speaks/types on the **next** turn (multi-turn dialog).
4. Work identically with or without a Groq/LLM key (offline path must stay demoable).

## 3. Non-goals

- No change to the Khata "Naya khaata" form.
- No phone format support beyond Indian 10-digit mobile numbers.
- No persistence of conversation state across server restarts (in-memory is fine).
- No editing/deleting accounts by voice (separate future feature).

## 4. Key design decision — deterministic front-controller

Account creation **and** the phone follow-up are handled by a deterministic
controller inside `brain.respond()`, evaluated **before** routing to the LLM or
the offline parser — mirroring the existing `_smalltalk()` early-return.

Rationale:

- A multi-step phone prompt is a state machine. Driving it deterministically is
  far more reliable for a live demo than relying on the LLM to manage it each turn.
- The LLM path does not currently pass conversation history, so it could not do
  the follow-up natively without a larger change.
- Keeps behaviour identical with and without an LLM key — a project principle
  (the offline path must remain fully functional).

The LLM continues to own everything else: ledger credit/debit, balance, list,
knowledge/RAG, reminders, weather, maths.

## 5. Conversation flow

```
USER: Ramesh aur Suresh aur Mukesh ka khaata banao
BOT:  ✅ 3 naye khaate ban gaye: Ramesh, Suresh, Mukesh.
      Ramesh ka phone number bataiye? (ya 'skip' boliye)
      [state: awaiting=phone, queue=[Ramesh, Suresh, Mukesh]]

USER: 9876543210
BOT:  ✅ Ramesh ka number save ho gaya. Ab Suresh ka phone number?

USER: skip
BOT:  Theek hai, chhod diya. Ab Mukesh ka phone number?

USER: 9123456780
BOT:  ✅ Ho gaya! Saare khaate taiyaar hain.
```

### Behaviour rules

- **Inline phone:** "Ramesh ka khaata banao 9876543210" saves the number at
  creation; no follow-up prompt for that name.
- **Already exists:** an existing name is not duplicated. In a batch, only the new
  names are created; the reply notes which already existed. Existing names are not
  added to the phone queue.
- **Skip words:** `skip, chhodo, chhod do, baad mein, rehne do, nahi, no, aage`.
- **Invalid number:** if the message during phone-capture looks like an attempted
  number but is not a valid 10-digit mobile, re-ask once for the same name.
- **Escape hatch:** if, while awaiting a phone, the message is clearly neither a
  phone nor a skip word (e.g. "Ramesh ko 500 udhaar likho"), the pending flow is
  abandoned and the message is processed normally. The user is never trapped.
- **Supplier detection:** default new accounts to `customer`; the words
  `supplier, vendor, dukaandaar, distributor, thok` make them `supplier`.

## 6. Components and changes

### `app/db.py`
- `set_party_phone(conn, party_id, phone)` — UPDATE party SET phone.
- `add_party` stays as-is; duplicate avoidance is handled by `get_or_create_party`
  (already case-insensitive). Creation helpers will reuse it so the same name is
  never duplicated.

### `app/tools.py`
- `create_accounts(conn, names, party_type="customer")` → returns
  `{"created": [...], "existing": [...]}` of party names, plus their ids.
- `set_phone(conn, party_name, phone)` → validates and stores; returns
  `{"party", "phone"}` or an error marker for an invalid number.

### `app/parser.py`
- New `create` intent. Detect creation verbs: `khaata/khata/account` followed by
  `banao/bana do/bana de/kholo/khol do/create/add account/naya khaata`.
- Extract names: strip the creation phrase, split the name span on `aur`, `,`,
  `and`, `&`. Drop stop-words and numbers.
- Optional inline phone: a 10-digit run in the command.
- Supplier keyword → `party_type="supplier"`.
- Must NOT fire when an amount + ledger verb is present ("Ramesh ko 500 udhaar
  likho" stays an `add`). Create requires a creation verb and no transaction amount.
- `Intent` dataclass gains: `names: list[str] | None`, `phone: str | None`
  (existing fields stay; `party` unused for create).

### `app/brain.py`
- A module-level `_SESSIONS: dict[str, dict]` conversation-state store, keyed by
  `session_id` (default `"default"`). Each entry holds at most one pending action:
  `{"awaiting": "phone", "queue": [party_id, ...], "retried": bool}`.
- `respond(message, lang="auto", conn=None, session_id="default")` — new optional
  `session_id` param.
- Front-controller order inside `respond`, after the empty-message guard:
  1. **Pending phone-capture** for this session → handle phone / skip / escape.
  2. `_smalltalk`.
  3. **Create intent** (via parser) → `create_accounts`, seed the phone queue from
     newly created ids, prompt for the first.
  4. Existing LLM / offline routing.
- Helpers: `_handle_phone_capture(state, message, conn)`, `_start_create(intent,
  conn, session_id)`, plus reply formatters consistent with the existing warm tone
  and ✅/📒 style.
- Phone validation helper: normalise (strip spaces, `-`, leading `+91`/`0`), accept
  exactly 10 digits starting 6–9.

### `app/llm.py`
- Add a `create_account` tool schema + dispatch (single name, optional phone,
  party_type) so natural phrasing with a key also creates. After the LLM creates
  via the tool, the brain's deterministic phone sub-dialog is NOT triggered for the
  LLM path in this phase (LLM replies in one turn); the offline path drives the
  multi-turn prompt. To keep behaviour identical and reliable, **create-intent is
  detected by the front-controller first**, so it is handled deterministically
  regardless of key. The LLM tool exists as a safety net for phrasings the parser
  misses; when it fires, the reply simply confirms creation without the queued
  follow-up.

### `app/main.py`
- `ChatRequest` gains optional `session_id: str = "default"`.
- `/chat` passes `session_id` to `brain.respond`.
- `/voice/chat` passes a `session_id` (form field, default `"default"`).

### `ui/_shared.py` and `ui/pages/2_🤖_AI_Assistant.py`
- Generate a per-session id once (`uuid4` in `st.session_state`) and send it with
  every `/chat` and `/voice/chat` call so the multi-turn phone dialog is correctly
  scoped to that browser session.

## 7. Data model

No schema change. Uses the existing `party.phone TEXT` column.

## 8. Testing (test-first)

Parser (`tests/test_parser.py` additions):
- single create, multi-name via `aur` / comma / `and`
- supplier keyword → supplier
- inline phone extracted
- "Ramesh ko 500 udhaar likho" stays `add` (no false create)

Tools / DB (`tests/test_tools.py`, `tests/test_db.py` additions):
- `create_accounts` creates new, reports existing, no duplicates
- `set_party_phone` writes; `set_phone` rejects invalid numbers

Brain state machine (`tests/test_brain.py` additions):
- full batch dialog: create 3 → phone, skip, phone → done
- invalid number re-asks once
- escape hatch: a ledger command during capture cancels the flow and is processed
- session isolation: two session_ids don't share a pending queue

End-to-end (`tests/test_api.py` or scripted): `/chat` with a `session_id` across
turns produces the correct multi-turn result and persists phones to the DB.

## 9. Risks

- **Parser false-positive/negative** on create vs. ledger intent. Mitigated by
  requiring a creation verb and absence of a transaction amount, plus explicit tests.
- **Name extraction** from free Hinglish ("mere dost Ramesh ka khaata"). Best-effort
  in the offline parser; the LLM tool covers the long tail when a key is present.
- **State leak / staleness** if a user abandons mid-flow. Mitigated by the escape
  hatch and by keeping only one pending action per session.

## 10. Delivery (cross-checked after every step, per project convention)

1. DB + tools layer (`set_party_phone`, `create_accounts`, `set_phone`) + tests.
2. Parser `create` intent + tests.
3. Brain front-controller + conversation state + phone sub-dialog + tests.
4. LLM `create_account` tool + dispatch + test.
5. API `session_id` plumbing + Streamlit session id.
6. End-to-end multi-turn verification (offline path; and with a key if available).
