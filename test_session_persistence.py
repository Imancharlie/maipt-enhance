"""Session store survives service restarts (the original data-loss bug)."""
import json
from datetime import datetime, timedelta

import pytest

from app.session import SessionManager


@pytest.fixture
def store(tmp_path):
    return tmp_path / "sessions.json"


def test_session_survives_restart(store):
    manager = SessionManager(store_path=str(store))
    session_id = manager.create_session(user_id=7, week_number=5, report_data={"week_number": 5})
    manager.update_session(session_id, conversation_history=[{"role": "user", "content": "hi"}],
                           status="ready")

    # Simulate a service restart: a brand-new manager reads the same file.
    reborn = SessionManager(store_path=str(store))
    session = reborn.get_session(session_id)
    assert session is not None
    assert session["user_id"] == 7
    assert session["week_number"] == 5
    assert session["conversation_history"] == [{"role": "user", "content": "hi"}]
    assert session["status"] == "ready"
    assert isinstance(session["created_at"], datetime)


def test_store_file_is_valid_json(store):
    manager = SessionManager(store_path=str(store))
    manager.create_session(user_id=1, week_number=1)
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert isinstance(list(payload.values())[0]["created_at"], str)


def test_corrupt_store_does_not_crash(tmp_path):
    store = tmp_path / "sessions.json"
    store.write_text("{not json", encoding="utf-8")
    manager = SessionManager(store_path=str(store))
    assert manager.sessions == {}
    # And the manager can still work afterwards.
    session_id = manager.create_session(user_id=2, week_number=3)
    assert manager.get_session(session_id) is not None


def test_expired_sessions_cleaned_on_load(store):
    manager = SessionManager(store_path=str(store))
    session_id = manager.create_session(user_id=1, week_number=1)
    # Age the stored session beyond the TTL.
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload[session_id]["created_at"] = (
        datetime.utcnow() - timedelta(minutes=180)
    ).isoformat()
    store.write_text(json.dumps(payload), encoding="utf-8")

    reborn = SessionManager(store_path=str(store))
    assert reborn.get_session(session_id) is None


def test_update_missing_session_is_noop(store):
    manager = SessionManager(store_path=str(store))
    manager.update_session("nope", status="ready")
    assert manager.get_session("nope") is None
