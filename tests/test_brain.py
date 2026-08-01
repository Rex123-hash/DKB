"""Tests for the brain's basic conversational behaviour."""
import pytest

from app import brain, db


@pytest.fixture
def conn():
    c = db.get_connection(":memory:")
    db.init_db(c)
    return c


@pytest.fixture(autouse=True)
def _clear_sessions():
    brain._SESSIONS.clear()
    yield
    brain._SESSIONS.clear()


def test_empty_message_gives_greeting():
    assert "Namaste" in brain.respond("   ")


def test_ledger_message_is_processed_not_echoed(conn):
    out = brain.respond("Ramesh ko 500 udhaar likho", conn=conn)
    # it acts on the message rather than echoing it verbatim
    assert "Ho gaya" in out
    assert "500" in out


def test_create_single_then_phone(conn):
    s = "sess1"
    r1 = brain.respond("Ramesh ka khaata banao", conn=conn, session_id=s)
    assert "Ramesh" in r1 and "phone" in r1.lower()
    r2 = brain.respond("9876543210", conn=conn, session_id=s)
    assert "save" in r2.lower()
    assert db.find_party_by_name(conn, "Ramesh")["phone"] == "9876543210"
    # session cleared after the single account is done
    assert s not in brain._SESSIONS


def test_create_batch_phone_skip_phone(conn):
    s = "batch"
    r1 = brain.respond("Ramesh aur Suresh aur Mukesh ka khaata banao", conn=conn, session_id=s)
    assert "3 naye khaate" in r1
    assert "Ramesh ka phone" in r1
    # Ramesh: give a number
    brain.respond("9876543210", conn=conn, session_id=s)
    # Suresh: skip
    r3 = brain.respond("skip", conn=conn, session_id=s)
    assert "Mukesh" in r3
    # Mukesh: give a number -> done
    r4 = brain.respond("9123456780", conn=conn, session_id=s)
    assert "Ho gaya" in r4
    assert db.find_party_by_name(conn, "Ramesh")["phone"] == "9876543210"
    assert db.find_party_by_name(conn, "Suresh")["phone"] is None
    assert db.find_party_by_name(conn, "Mukesh")["phone"] == "9123456780"


def test_invalid_phone_reasks_once(conn):
    s = "inv"
    brain.respond("Ramesh ka khaata banao", conn=conn, session_id=s)
    r = brain.respond("12345", conn=conn, session_id=s)  # 5 digits, invalid mobile
    assert "dobara" in r.lower()
    assert brain._SESSIONS[s]["retried"] is True


def test_escape_hatch_cancels_capture(conn):
    s = "esc"
    brain.respond("Ramesh ka khaata banao", conn=conn, session_id=s)
    # a clear ledger command during capture should be processed, not eaten
    out = brain.respond("Suresh ko 500 udhaar likho", conn=conn, session_id=s)
    assert "Ho gaya" in out and "500" in out
    assert s not in brain._SESSIONS
    assert db.find_party_by_name(conn, "Suresh") is not None


def test_sessions_are_isolated(conn):
    brain.respond("Ramesh ka khaata banao", conn=conn, session_id="a")
    # session b knows nothing about a's pending phone capture
    assert "b" not in brain._SESSIONS
    out_b = brain.respond("9876543210", conn=conn, session_id="b")
    # b treats the bare number as not-a-ledger-command (falls through)
    assert "save" not in out_b.lower()


def test_llm_create_hands_off_to_phone_capture(conn, monkeypatch):
    # Simulate a phrasing the parser misses, where the LLM creates via its tool.
    from app import llm

    def fake_llm_run(message, c, lang="auto", max_steps=5, created_sink=None,
                     reminder_sink=None):
        from app import tools
        res = tools.create_accounts(c, ["Suryaa"])
        if created_sink is not None:
            created_sink.extend(res["created"])
        return "Suryaa ka khaata ban gaya hai, account ID 99."  # ugly LLM phrasing

    monkeypatch.setenv("GEMINI_API_KEY", "x")  # make config.has_llm() true
    monkeypatch.setattr(llm, "run", fake_llm_run)

    s = "llmsess"
    r1 = brain.respond("craete an account for Suryaa", conn=conn, session_id=s)
    # our clean format + phone prompt, NOT the LLM's "account ID 99" text
    assert "account ID" not in r1
    assert "Suryaa" in r1 and "phone" in r1.lower()
    assert brain._SESSIONS[s]["awaiting"] == "phone"
    r2 = brain.respond("9876543210", conn=conn, session_id=s)
    assert "save" in r2.lower()
    assert db.find_party_by_name(conn, "Suryaa")["phone"] == "9876543210"


def test_inline_phone_at_creation(conn):
    out = brain.respond("Mohan ka khaata banao 9876500000", conn=conn, session_id="z")
    assert "save" in out.lower()
    assert db.find_party_by_name(conn, "Mohan")["phone"] == "9876500000"
    assert "z" not in brain._SESSIONS


def test_set_phone_for_existing_party_via_chat(conn):
    from app import tools
    tools.create_accounts(conn, ["Aman"])
    out = brain.respond("Aman ka phone 9876543210", conn=conn)
    assert "save" in out.lower()
    assert db.find_party_by_name(conn, "Aman")["phone"] == "9876543210"


def test_set_phone_unknown_party(conn):
    out = brain.respond("Nobody ka phone 9876543210", conn=conn)
    assert "nahi mila" in out.lower()


def test_offline_reminder_creates_row_without_reading_url(conn):
    from app import tools
    tools.create_accounts(conn, ["Rahul"])
    tools.set_phone(conn, "Rahul", "9876543210")
    out = brain.respond("Rahul ko kal 5 baje 5000 ke payment ke liye call karna", conn=conn)
    assert "call reminder laga diya" in out
    assert "http" not in out  # never read the WhatsApp URL aloud
    rows = db.list_reminders(conn)
    assert len(rows) == 1
    assert rows[0]["amount"] == 5000
    assert rows[0]["phone"] == "9876543210"


def test_reminder_without_phone_waits_for_phone_or_skip(conn):
    from app import tools
    tools.create_accounts(conn, ["Sita"])
    s = "ordinary-reminder"

    first = brain.respond("Sita ko kal 5 baje 500 ka payment reminder lagao", conn=conn, session_id=s)
    assert "10-digit" in first and "skip" in first.lower()
    assert db.list_reminders(conn) == []

    invalid = brain.respond("1234", conn=conn, session_id=s)
    assert "theek nahi" in invalid.lower()
    assert db.list_reminders(conn) == []

    done = brain.respond("9123456780", conn=conn, session_id=s)
    assert "reminder laga diya" in done
    assert db.list_reminders(conn)[0]["phone"] == "9123456780"


def test_reminder_without_phone_allows_explicit_skip(conn):
    from app import tools
    tools.create_accounts(conn, ["Mohan"])
    s = "ordinary-reminder-skip"

    brain.respond("Mohan ko kal 5 baje 500 ke liye call karna", conn=conn, session_id=s)
    not_skipped = brain.respond("number nahi hai", conn=conn, session_id=s)
    assert "skip" in not_skipped.lower()
    assert db.list_reminders(conn) == []
    done = brain.respond("skip", conn=conn, session_id=s)

    assert "reminder laga diya" in done
    assert db.list_reminders(conn)[0]["phone"] is None


def test_reminder_pronoun_asks_party_then_missing_amount(conn):
    s = "pronoun-reminder"
    first = brain.respond("mujhe kal 5 baje payment yaad dilana", conn=conn, session_id=s)
    assert "kiske liye" in first.lower()
    assert db.find_party_by_name(conn, "mujhe") is None

    second = brain.respond("Aman ke liye", conn=conn, session_id=s)
    assert "amount" in second.lower()
    assert db.list_reminders(conn) == []

    third = brain.respond("750", conn=conn, session_id=s)
    assert "10-digit" in third
    done = brain.respond("skip", conn=conn, session_id=s)
    assert "reminder laga diya" in done
    assert db.list_reminders(conn)[0]["amount"] == 750


def test_party_followup_can_include_amount_without_repeat_question(conn):
    s = "party-and-amount"
    brain.respond("mujhe kal 5 baje reminder lagao", conn=conn, session_id=s)
    reply = brain.respond("Suresh ke liye 900", conn=conn, session_id=s)
    assert "10-digit" in reply
    assert "amount" not in reply.lower()


def test_reminder_asks_missing_time_instead_of_defaulting_to_ten(conn):
    from app import tools
    tools.create_accounts(conn, ["Ram"])
    tools.set_phone(conn, "Ram", "9876543210")
    s = "missing-time"

    first = brain.respond("Ram ko kal 500 ka reminder lagao", conn=conn, session_id=s)
    assert "kis time" in first.lower()
    assert "10:00" not in first
    assert db.list_reminders(conn) == []

    done = brain.respond("shaam 6 baje", conn=conn, session_id=s)
    assert "reminder laga diya" in done
    assert db.list_reminders(conn)[0]["due_at"].endswith("18:00")


def test_reminder_asks_missing_date_and_preserves_given_time(conn):
    from app import tools
    tools.create_accounts(conn, ["Riya"])
    tools.set_phone(conn, "Riya", "9876543210")
    s = "missing-date"

    first = brain.respond("Riya ko 6 PM 800 ka reminder lagao", conn=conn, session_id=s)
    assert "kis date" in first.lower()
    assert db.list_reminders(conn) == []

    done = brain.respond("kal", conn=conn, session_id=s)
    assert "reminder laga diya" in done
    assert db.list_reminders(conn)[0]["due_at"].endswith("18:00")


# --- guided "maango" call-reminder dialog (name -> [number] -> purpose -> time) ---
def test_collect_flow_known_phone_asks_purpose_then_time_then_saves(conn):
    from app import tools
    tools.create_accounts(conn, ["Rahul"])
    tools.set_phone(conn, "Rahul", "9876543210")
    s = "collect1"
    r1 = brain.respond("Rahul se 500 rupye maango", conn=conn, session_id=s)
    assert "purpose" in r1.lower()
    assert "9876543210" in r1               # confirms the number back
    assert brain._SESSIONS[s]["awaiting"] == "reminder_purpose"

    r2 = brain.respond("purana udhaar", conn=conn, session_id=s)
    assert "samay" in r2.lower()            # asks for the call time
    assert brain._SESSIONS[s]["awaiting"] == "reminder_time"

    r3 = brain.respond("kal 4 baje", conn=conn, session_id=s)
    assert "reminder" in r3.lower()         # confirms it will show in Reminders
    assert "http" not in r3                 # never read a URL aloud
    assert s not in brain._SESSIONS         # dialog finished, session cleared

    rows = db.list_reminders(conn)
    assert len(rows) == 1
    assert rows[0]["amount"] == 500
    assert rows[0]["channel"] == "call"
    assert rows[0]["message"] == "purana udhaar"
    assert rows[0]["phone"] == "9876543210"


def test_collect_flow_asks_for_number_when_missing(conn):
    s = "collect2"
    r1 = brain.respond("Sita se 300 maango", conn=conn, session_id=s)   # Sita is new
    assert "number" in r1.lower()
    assert brain._SESSIONS[s]["awaiting"] == "reminder_phone"

    r2 = brain.respond("9123456780", conn=conn, session_id=s)
    assert "purpose" in r2.lower()

    brain.respond("bakaya payment", conn=conn, session_id=s)
    brain.respond("parso 11 baje", conn=conn, session_id=s)

    rows = db.list_reminders(conn)
    assert len(rows) == 1
    assert rows[0]["amount"] == 300
    assert rows[0]["phone"] == "9123456780"
    assert db.find_party_by_name(conn, "Sita")["phone"] == "9123456780"


def test_collect_does_not_record_a_ledger_transaction(conn):
    from app import tools
    tools.create_accounts(conn, ["Rahul"])
    tools.set_phone(conn, "Rahul", "9876543210")
    brain.respond("Rahul se 500 maango", conn=conn, session_id="c3")
    # 'maango' starts a reminder, it must NOT be recorded as udhaar/credit
    assert tools.get_party_balance(conn, "Rahul")["balance"] == 0
    assert db.list_reminders(conn) == []   # nothing saved until the flow completes


def test_collect_flow_devanagari_voice(conn):
    # Voice transcribes in Devanagari: "Riya se paanso rupaye maango"
    s = "dev1"
    r1 = brain.respond("रिया से पान्सो रूपे मांगो", conn=conn, session_id=s)
    # new party (no phone) -> the guided flow asks for the number first
    assert brain._SESSIONS[s]["awaiting"] == "reminder_phone"
    r2 = brain.respond("9876543210", conn=conn, session_id=s)
    assert "500" in r2          # amount 'paanso' understood as 500, echoed in purpose Q
    brain.respond("purana udhaar", conn=conn, session_id=s)
    brain.respond("कल शाम 5 बजे", conn=conn, session_id=s)   # Devanagari time
    rows = db.list_reminders(conn)
    assert len(rows) == 1
    assert rows[0]["amount"] == 500
    assert rows[0]["channel"] == "call"
    assert rows[0]["due_at"].endswith("17:00")   # shaam 5 -> 17:00


def test_collect_can_be_cancelled(conn):
    from app import tools
    tools.create_accounts(conn, ["Rahul"])
    tools.set_phone(conn, "Rahul", "9876543210")
    s = "c4"
    brain.respond("Rahul se 500 maango", conn=conn, session_id=s)
    out = brain.respond("cancel", conn=conn, session_id=s)
    assert "cancel" in out.lower()
    assert s not in brain._SESSIONS
    assert db.list_reminders(conn) == []
