from datetime import datetime, timedelta
from typing import Dict, Optional
import uuid
from app.schemas import SessionData

class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
    
    def create_session(self, user_id: int, week_number: int, report_data: Dict = None) -> str:
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
        return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        return self.sessions.get(session_id)
    
    def update_session(self, session_id: str, **kwargs):
        if session_id in self.sessions:
            for key, value in kwargs.items():
                self.sessions[session_id][key] = value
    
    def cleanup_expired_sessions(self, ttl_minutes: int):
        cutoff = datetime.utcnow() - timedelta(minutes=ttl_minutes)
        expired = [
            sid for sid, session in self.sessions.items()
            if session["created_at"] < cutoff
        ]
        for sid in expired:
            del self.sessions[sid]

session_manager = SessionManager()
