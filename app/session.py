import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

from app.config import settings


class SessionManager:
    """In-memory sessions that survive service restarts.

    Every mutation is flushed to a JSON store on disk, so a crash, redeploy
    or uvicorn --reload never wipes a student's conversation.
    """

    def __init__(self, store_path: Optional[str] = None):
        self.sessions: Dict[str, Dict] = {}
        self.store_path = Path(store_path or settings.SESSION_STORE_PATH)
        self._lock = threading.Lock()
        self.load()

    # -- persistence -----------------------------------------------------

    def load(self) -> None:
        with self._lock:
            if not self.store_path.exists():
                return
            try:
                raw = json.loads(self.store_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt store must never take the service down.
                return
            if not isinstance(raw, dict):
                return
            self.sessions = {}
            for session_id, session in raw.items():
                if not isinstance(session, dict):
                    continue
                created = session.get("created_at")
                if isinstance(created, str):
                    try:
                        created = datetime.fromisoformat(created)
                    except ValueError:
                        created = datetime.utcnow()
                elif not isinstance(created, datetime):
                    created = datetime.utcnow()
                session["created_at"] = created
                self.sessions[session_id] = session
            self._cleanup_locked()

    def _save_locked(self) -> None:
        payload = {}
        for session_id, session in self.sessions.items():
            stored = dict(session)
            created = stored.get("created_at")
            if isinstance(created, datetime):
                stored["created_at"] = created.isoformat()
            payload[session_id] = stored
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.store_path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, default=str)
                os.replace(tmp_path, self.store_path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError:
            # Disk issues must not break live conversations; memory still works.
            pass

    def _cleanup_locked(self, ttl_minutes: Optional[int] = None) -> None:
        ttl = ttl_minutes if ttl_minutes is not None else settings.SESSION_TTL_MINUTES
        cutoff = datetime.utcnow() - timedelta(minutes=ttl)
        expired = [
            session_id
            for session_id, session in self.sessions.items()
            if session.get("created_at") < cutoff
        ]
        for session_id in expired:
            del self.sessions[session_id]
        return bool(expired)

    # -- API ---------------------------------------------------------------

    def create_session(self, user_id: int, week_number: int, report_data: Dict = None) -> str:
        with self._lock:
            session_id = str(uuid.uuid4())
            self.sessions[session_id] = {
                "session_id": session_id,
                "user_id": user_id,
                "week_number": week_number,
                "report_data": report_data or {},
                "conversation_history": [],
                "missing_info": [],
                "status": "in_progress",
                "created_at": datetime.utcnow()
            }
            self._cleanup_locked()
            self._save_locked()
            return session_id

    def get_session(self, session_id: str) -> Optional[Dict]:
        with self._lock:
            return self.sessions.get(session_id)

    def update_session(self, session_id: str, **kwargs):
        with self._lock:
            if session_id in self.sessions:
                for key, value in kwargs.items():
                    self.sessions[session_id][key] = value
                self._save_locked()

    def cleanup_expired_sessions(self, ttl_minutes: int):
        with self._lock:
            self._cleanup_locked(ttl_minutes)
            self._save_locked()


session_manager = SessionManager()
