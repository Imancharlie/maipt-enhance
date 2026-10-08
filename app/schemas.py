from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class WeeklyReportData(BaseModel):
    week_number: int
    main_job_title: str
    daily_reports: List[Dict[str, Any]]
    operations: List[Dict[str, Any]]
    user_program: str
    company_name: str
    additional_instructions: Optional[str] = ""

class EnhancementRequest(BaseModel):
    user_id: int  # For tracking only
    report_data: Optional[WeeklyReportData] = None  # Optional for conversational mode
    session_id: Optional[str] = None
    user_message: Optional[str] = None  # User's response in conversation
    mode: str = "enhance"  # "enhance" or "converse"
    conversation_history: Optional[List[Dict[str, str]]] = None  # Prior chat used to seed a resumed session

class AnswerRequest(BaseModel):
    session_id: str
    answer: Optional[str] = None
    confirm: bool = False  # User pressed the "Enhance report" button
    report_data: Optional[WeeklyReportData] = None  # Fresh snapshot of the student's week

class EnhancementResponse(BaseModel):
    success: bool
    session_id: str
    enhanced_data: Optional[Dict[str, Any]] = None
    question: Optional[str] = None  # For interactive mode
    ai_message: Optional[str] = None  # AI's conversational response
    tokens_used: Optional[int] = None
    error: Optional[str] = None
    is_complete: bool = False  # Whether enhancement is complete
    ready_to_enhance: bool = False  # AI has enough data and awaits the user's go-ahead
    suggestions: Optional[List[str]] = None  # Quick reply options for the current question
    week_number: Optional[int] = None  # Week tied to the session
    user_id: Optional[int] = None  # Owner of the session (tracking/verification)

class SessionData(BaseModel):
    session_id: str
    user_id: int
    week_number: int
    report_data: Optional[Dict[str, Any]] = None
    conversation_history: List[Dict[str, str]]
    missing_info: List[str] = []  # List of missing information to collect
    status: str  # "in_progress", "completed", "failed"
    created_at: datetime
