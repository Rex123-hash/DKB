"""Tests for Phase 4 — reminders + general assistant tools."""
from app import db, general, tools


def _fresh():
    conn = db.get_connection(":memory:")
    db.init_db(conn)
    return conn


# --- reminders ---
def test_schedule_and_list_reminder():
    conn = _fresh()
    res = tools.schedule_reminder(conn, "Ramesh", "2026-06-20T10:00:00", "udhaar yaad dilao")
    assert res["party"] == "Ramesh"
    pending = tools.list_reminders(conn, "pending")
    assert len(pending) == 1
    assert pending[0]["party_name"] == "Ramesh"
    assert pending[0]["message"] == "udhaar yaad dilao"


def test_mark_reminder_done():
    conn = _fresh()
    rid = tools.schedule_reminder(conn, "Ramesh", "2026-06-20T10:00:00")["id"]
    db.mark_reminder_done(conn, rid)
    assert tools.list_reminders(conn, "pending") == []
    assert len(tools.list_reminders(conn, "done")) == 1


# --- calculator ---
def test_calculate_basic():
    assert general.calculate("2 + 3 * 4")["result"] == 14
    assert general.calculate("(10 - 4) / 2")["result"] == 3
    assert general.calculate("2 ** 8")["result"] == 256


def test_calculate_rejects_code():
    out = general.calculate("__import__('os').system('echo hi')")
    assert "error" in out


# --- weather (mocked network) ---
def test_get_weather(monkeypatch):
    class FakeResp:
        def __init__(self, data):
            self._data = data
        def json(self):
            return self._data

    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "geocoding" in url:
            return FakeResp({"results": [{"name": "Kanpur", "latitude": 26.4, "longitude": 80.3}]})
        return FakeResp({"current": {"temperature_2m": 34.5, "weather_code": 0, "wind_speed_10m": 12}})

    monkeypatch.setattr(general.requests, "get", fake_get)
    w = general.get_weather("Kanpur")
    assert w["city"] == "Kanpur"
    assert w["temperature_c"] == 34.5
    assert "clear" in w["weather"]
