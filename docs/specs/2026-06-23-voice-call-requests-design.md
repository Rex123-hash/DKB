# Voice-Created Call Requests (Enriched Reminders) — Design

Date: 2026-06-23
Status: Approved (ready for implementation plan)
Feature phase: Phase 7 (builds on the existing Reminders module and voice path)

## 1. Problem

The mentor wants shopkeepers to create **call requests / call reminders by speaking
naturally** (Hindi / English / Hinglish), instead of filling the manual "Request a
Call" form (Name, Phone, Amount, Message). The spoken request must be parsed into a
structured record and stored so it can drive: the existing reminder workflow now,
WhatsApp/text follow-up now, and AI-calling + human-telecaller workflows later.

The app already has a **Reminders** module very close to this:
- `reminder` table: `id, party_id, due_at, message, status`.
- `tools.schedule_reminder(party_name, due_at, message)` and `list_reminders`.
- The LLM brain already has a `schedule_reminder` tool, with the current date-time
  injected into the system prompt so relative dates ("kal", "Monday 10 baje") resolve.
- Voice already flows `mic -> /voice/chat -> brain.respond`.
- A Reminders Streamlit page (`ui/pages/3_⏰_Reminders.py`) lists pending reminders.

A "call request" is therefore a **reminder-to-call with money + contact attached**.

## 2. Decisions (locked)

1. **Extend the existing `reminder` table** rather than create a separate
   `call_request` table. One unified list and workflow; no duplication.
2. **Store + generate a no-key WhatsApp link.** Voice creates and stores the
   structured request; we build a `wa.me` click-to-send link the shopkeeper taps.
   No Twilio / WhatsApp Business API and no automated calling in this task — the
   mentor said those are not required now. Data is designed to support them later.

## 3. Goals

1. Create a structured call request from natural voice/text in Hindi/English/Hinglish.
2. Extract: party name, amount, description, scheduled date + time. Reuse the party's
   phone from existing records; snapshot it onto the request.
3. Store all fields the mentor listed, in the existing reminder workflow.
4. Offer a no-key WhatsApp click-to-send link on the Reminders page.
5. Keep the stored row sufficient for future AI-calling and telecaller workflows.

## 4. Non-goals

- No Twilio / WhatsApp Business API integration; no automated calling; no telecaller UI.
- No multi-user auth (single-shopkeeper prototype; `created_by` is a constant for now).
- No change to the manual Reminders create form's existing behaviour beyond adding the
  new Amount / Channel fields.

## 5. Data model — extend `reminder`

New nullable columns added by an idempotent migration (see §6):

| Column       | Type | Default   | Meaning |
|--------------|------|-----------|---------|
| `amount`     | REAL | NULL      | pending rupee amount, if mentioned |
| `channel`    | TEXT | `'call'`  | `'call'` or `'whatsapp'` — intended follow-up |
| `phone`      | TEXT | NULL      | snapshot of the party's phone at creation time |
| `created_by` | TEXT | `'owner'` | user association; constant now, ready for multi-user |
| `created_at` | TEXT | (now)     | request-creation timestamp (distinct from `due_at`) |

Existing columns keep their meaning: `party_id` (customer/supplier reference),
`due_at` (ISO 8601 scheduled date-time), `message` (description), `status`
(`pending`/`done`). This is exactly the mentor's field list and is enough for the
future AI-calling / telecaller use cases (a worker reads `pending` rows).

`channel` gets a CHECK constraint only on fresh creates via app code, not on the
migrated column (SQLite cannot add a CHECK via ALTER) — app code always writes a
valid value, and `add_reminder` validates.

## 6. Schema migration (existing dukanbook.db)

`init_db` runs `SCHEMA` (CREATE TABLE IF NOT EXISTS) then a `_migrate(conn)` step:
for each new column, check `PRAGMA table_info(reminder)`; if absent,
`ALTER TABLE reminder ADD COLUMN ...`. Idempotent and safe on both fresh and existing
DBs. New installs get the columns directly in `SCHEMA`; the migration is a no-op there.

## 7. Components and changes

### `app/db.py`
- Add the five columns to the `reminder` block in `SCHEMA`.
- Add `_migrate(conn)` called by `init_db` after `executescript(SCHEMA)`.
- Extend `add_reminder(conn, party_id, due_at, message=None, amount=None,
  channel="call", phone=None, created_by="owner")`. Validates `channel in
  ('call','whatsapp')`; stamps `created_at = _now()`.
- `list_reminders` SELECT includes the new columns (and `party_name`, already joined).

### `app/tools.py`
- `whatsapp_link(phone, text)` -> `https://wa.me/91<10-digit>?text=<urlencoded>` or
  `None` if phone is missing/invalid (reuses `normalize_phone`).
- Extend `schedule_reminder(conn, party_name, due_at, message=None, amount=None,
  channel="call")`: `get_or_create_party`, snapshot the party's phone onto the
  request, build the WhatsApp link, return
  `{"id","party","due_at","amount","channel","phone","message","whatsapp_link"}`.

### `app/llm.py`
- Extend the `schedule_reminder` tool schema with optional `amount` (number),
  `description` (string, maps to `message`), and `channel` (enum call/whatsapp).
- `_dispatch` maps `description` -> `message` and passes `amount`/`channel`.
- One added system-prompt line: when a follow-up/call/payment reminder is requested,
  capture the amount and a short description, and resolve the date-time from the
  injected current date-time.

### `app/parser.py` (light offline fallback)
- New `reminder` intent (best-effort, no key): trigger words `yaad dilao/dila do/
  reminder/call karna/call karo/follow up/followup`. Extract party (existing
  `_extract_party`), amount (existing `_extract_amount`), and a simple date-time via a
  new `_extract_due(text)` helper: `aaj`=today, `kal`=tomorrow, `parso`=+2 days,
  weekday names = next such weekday, `"N baje"`/`"N pm/am"` = hour (default 10:00).
  Returns `Intent("reminder", party=..., amount=..., due_at=<ISO>, message=<text>)`.
  `Intent` dataclass gains `due_at: str | None = None`.
- The LLM remains the primary, reliable path; the offline fallback keeps the feature
  demoable without a key for common phrasings.

### `app/brain.py`
- Offline path (`_offline_respond`): handle `intent.action == "reminder"` -> call
  `tools.schedule_reminder` -> warm confirmation via a new `_fmt_reminder(res)`
  ("✅ {party} ko {when} {amount?} ke liye call reminder laga diya."). Does NOT read
  the WhatsApp URL aloud; the link surfaces on the Reminders page.
- No change to the create-account front-controller; reminders are a normal intent.

### `app/main.py`
- `ReminderIn` gains optional `amount: float | None`, `channel` (call/whatsapp),
  `message` already present. `POST /reminders` passes them to `db.add_reminder`.
- `GET /reminders` returns full rows (including new columns) and adds a computed
  `whatsapp_link` field per row via `tools.whatsapp_link(row.phone, row.message)`
  (`None` when no phone). The link is derived, not stored — single source of truth.

### `ui/pages/3_⏰_Reminders.py` and `ui/_shared.py`
- Create form: add **Amount (₹)** number input and **Channel** radio (call/whatsapp).
- `_shared.create_reminder(...)` gains `amount`, `channel`.
- Each pending reminder row shows the amount and, when the row's `whatsapp_link` is
  present, a **📲 WhatsApp** link button (`st.link_button`) to that URL, plus the
  existing **Done**. No client-side link logic — the UI uses the API-provided field.

## 8. WhatsApp link format

`https://wa.me/91<phone>?text=<urlencoded message>` where the default prefilled text is
a short Hinglish reminder, e.g.:
`"Namaste {name} ji 🙏, aapka ₹{amount} ka payment pending hai. Kripya jaldi clear
karein. Dhanyavaad — {shop}"` (amount/shop omitted gracefully if absent). The shopkeeper
taps it; their own WhatsApp opens with the message pre-filled to send. No key, no cost.

## 9. Data flow

```
voice/text -> /voice/chat (or /chat) -> brain.respond
   -> LLM schedule_reminder tool (amount, description, channel, due_at)   [primary]
      or offline parser 'reminder' intent                                  [fallback]
   -> tools.schedule_reminder -> db.add_reminder (+ phone snapshot, created_at)
   -> reply: spoken/text confirmation (NO url)
Reminders page -> GET /reminders -> shows amount + 📲 WhatsApp link + Done
```

## 10. Testing (test-first, cross-checked each step)

- `db.py`: migration adds columns on an old-style table; `add_reminder` stores all
  fields + `created_at`; invalid channel rejected.
- `tools.py`: `whatsapp_link` builds correct URL / returns None for bad phone;
  `schedule_reminder` snapshots phone and returns the link.
- `parser.py`: `reminder` intent for "Ramesh ko kal call karna 5000" (party/amount/
  due_at tomorrow); weekday + "N baje"; not misfired by plain ledger adds.
- `llm.py`: faked tool-call with amount/description/channel persists a full row.
- `brain.py`: offline reminder phrasing creates a row and confirms without a URL.
- API/e2e: `POST /reminders` with amount/channel; `GET /reminders` returns them;
  voice/text "kal Rahul ko 5000 ke liye call reminder" creates a pending row.

## 11. Risks

- **Offline date parsing is limited.** Mitigated: the LLM is the primary path and
  handles the long tail; the offline helper covers only common phrasings and defaults
  the time to 10:00 when unspecified.
- **Phone missing** -> no WhatsApp link. Handled: request still stored; UI shows
  "phone add karein" instead of the button; account-creation feature can capture it.
- **Migration on a live DB.** Mitigated: idempotent `PRAGMA`-guarded ALTERs; tested
  against an old-style table.

## 12. Delivery (cross-checked after every step)

1. `db.py` schema + migration + richer `add_reminder` + tests.
2. `tools.py` `whatsapp_link` + enriched `schedule_reminder` + tests.
3. `parser.py` offline `reminder` intent + `_extract_due` + tests.
4. `brain.py` offline reminder handling + tests.
5. `llm.py` tool schema/dispatch/prompt + test.
6. `main.py` + `_shared.py` + Reminders page (amount, channel, WhatsApp button).
7. End-to-end verification (voice/text -> stored enriched row -> WhatsApp link in UI).
