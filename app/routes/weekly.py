import re

from fastapi import APIRouter, Depends
from app.auth import verify_api_key
from app.schemas import AnswerRequest, EnhancementRequest, EnhancementResponse
from app.ai import READY_FALLBACK_MESSAGE, _clean_suggestions, claude_service
from app.session import session_manager
from app.analytics import analytics_tracker

router = APIRouter()

KICKOFF_MESSAGE = "Please start enhancing my weekly report. Ask me your first questions."
CONTINUE_MESSAGE = ("Please continue enhancing my weekly report using everything from our "
                    "earlier conversation above.")
CONFIRM_MESSAGE = "Yes, enhance my report now."
COMPLETION_MESSAGE = ("Enhancement completed! I've produced the final version of your "
                      "weekly report using everything you told me.")

# Typed confirmation while the AI is waiting for the go-ahead (the button sends
# confirm=true instead, which is authoritative).
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yep|yeah|y|ok|okay|sure|fine|perfect|proceed|confirm(?:ed)?|"
    r"go ahead|go for it|do it|that'?s (?:all|good)|"
    r"no more(?: (?:questions|details))?|"
    r"looks good|all good|i'?m done|done|ready)\b",
    re.IGNORECASE
)
_AFFIRMATIVE_PHRASE_RE = re.compile(
    r"\b(?:enhance|proceed with|go ahead with|finish)\b\s*(?:it|now|my report|the report|this)\b",
    re.IGNORECASE
)
_ENHANCE_REQUEST_RE = re.compile(
    r"\b(?:enhance|prepare|create|generate|write|make)\b\s*(?:my|the|a)?\s*(?:report|enhanced report|final report)\b",
    re.IGNORECASE
)


def _is_affirmative(text: str) -> bool:
    text = (text or "").strip()
    return bool(_AFFIRMATIVE_RE.match(text) or _AFFIRMATIVE_PHRASE_RE.search(text))


def _is_enhance_request(text: str) -> bool:
    """Check if user is requesting enhancement/report creation."""
    text = (text or "").strip()
    return bool(_ENHANCE_REQUEST_RE.search(text))


def _sanitize_history(raw) -> list:
    """Turn a client supplied transcript into a usable seeded conversation."""
    if not isinstance(raw, list):
        return []
    seeded = []
    for message in raw[-60:]:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        # Kept whole: truncating a message here silently loses what the student said.
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            seeded.append({"role": role, "content": content.strip()})
    return seeded


@router.post("/enhance", response_model=EnhancementResponse)
async def enhance_weekly_report(
    request: EnhancementRequest,
    authenticated: bool = Depends(verify_api_key)
):
    # Create or get session
    if not request.session_id:
        if not request.report_data:
            return EnhancementResponse(
                success=False,
                session_id="",
                error="Report data required for new session"
            )
        session_id = session_manager.create_session(
            request.user_id,
            request.report_data.week_number,
            request.report_data.model_dump()
        )
        seeded = _sanitize_history(request.conversation_history)
        if seeded:
            # A resumed week: Claude starts with the earlier chat as context.
            session_manager.update_session(
                session_id, conversation_history=seeded, needs_kickoff=True
            )
    else:
        session_id = request.session_id
        session = session_manager.get_session(session_id)
        if not session:
            return EnhancementResponse(
                success=False,
                session_id="",
                error="Invalid session ID"
            )
        if request.report_data:
            # The client re-sends the student's week on every call, so the
            # session never keeps an outdated (e.g. still empty) snapshot.
            session_manager.update_session(
                session_id, report_data=request.report_data.model_dump()
            )

    if request.mode == "converse":
        session = session_manager.get_session(session_id)
        if request.user_message:
            return await handle_answer(
                session_id, request.user_message, report_data=request.report_data
            )
        history = session.get("conversation_history") or []
        if session.get("needs_kickoff") or not history:
            kickoff = CONTINUE_MESSAGE if history else KICKOFF_MESSAGE
            history = history + [{"role": "user", "content": kickoff}]
            session_manager.update_session(
                session_id, conversation_history=history, needs_kickoff=False
            )
            return await run_turn(session_id, history)
        # Resumed session with no new answer: replay the pending question.
        return pending_response(session_id, session)

    return await handle_enhancement(session_id, request)


@router.post("/answer", response_model=EnhancementResponse)
async def answer(
    request: AnswerRequest,
    authenticated: bool = Depends(verify_api_key)
):
    """Continue a multi-turn enhancement conversation with a real user answer."""
    return await handle_answer(
        request.session_id, request.answer or "",
        confirm=request.confirm, report_data=request.report_data
    )


@router.get("/session/{session_id}")
async def session_status(session_id: str, authenticated: bool = Depends(verify_api_key)):
    """Cheap liveness check so clients know whether a session can be resumed."""
    session = session_manager.get_session(session_id)
    if not session:
        return {"exists": False, "session_id": session_id}
    return {
        "exists": True,
        "session_id": session_id,
        "status": session.get("status"),
        "week_number": session.get("week_number"),
        "user_id": session.get("user_id"),
    }


async def handle_answer(
    session_id: str,
    answer_text: str,
    confirm: bool = False,
    report_data=None
) -> EnhancementResponse:
    session = session_manager.get_session(session_id)
    if not session:
        return EnhancementResponse(success=False, session_id="", error="Invalid session ID")

    if report_data is not None:
        # Keep the session's report snapshot in sync with the saved week.
        session_manager.update_session(session_id, report_data=report_data.model_dump())

    if session.get("status") == "completed" and session.get("enhanced_data"):
        # Idempotent: a retry after completion returns the stored result.
        return EnhancementResponse(
            success=True,
            session_id=session_id,
            enhanced_data=session["enhanced_data"],
            ai_message=session.get("completion_message", COMPLETION_MESSAGE),
            is_complete=True,
            week_number=session["week_number"],
            user_id=session["user_id"]
        )

    draft = session.get("pending_enhanced_data")
    if confirm:
        # The user pressed the "Enhance report" button: save the drafted report.
        if session.get("status") == "ready" and isinstance(draft, dict) and draft:
            return finalize_session(session_id, session, draft, CONFIRM_MESSAGE)
        return EnhancementResponse(
            success=False,
            session_id=session_id,
            error="The report is not ready to enhance yet - please answer the questions first."
        )

    answer_text = (answer_text or "").strip()
    if not answer_text:
        return EnhancementResponse(
            success=False,
            session_id=session_id,
            error="Answer cannot be empty"
        )

    # If user selects "Enhance with what you have" option
    if session.get("status") == "in_progress" and answer_text.lower() == "enhance with what you have":
        # Force Claude to generate enhancement with available data
        history = list(session["conversation_history"])
        history.append({"role": "user", "content": "Please enhance my report with the information available, even if some details are missing."})
        return await run_turn(session_id, history)

    # If user requests enhancement while still in question phase, offer to enhance with available data
    if session.get("status") == "in_progress" and _is_enhance_request(answer_text):
        # Let Claude handle this - it will provide a question response with the enhance option
        history = list(session["conversation_history"])
        history.append({"role": "user", "content": answer_text})
        return await run_turn(session_id, history)

    # A typed go-ahead also finalizes the draft without another Claude call.
    if session.get("status") == "ready" and isinstance(draft, dict) and draft \
            and _is_affirmative(answer_text):
        return finalize_session(session_id, session, draft, answer_text)

    history = list(session["conversation_history"])
    history.append({"role": "user", "content": answer_text})
    return await run_turn(session_id, history)


def finalize_session(
    session_id: str,
    session: dict,
    enhanced_data: dict,
    user_message: str
) -> EnhancementResponse:
    """Complete a session with the drafted report (no extra Claude call)."""
    history = list(session.get("conversation_history") or [])
    history.append({"role": "user", "content": user_message})
    session_manager.update_session(
        session_id,
        conversation_history=history,
        status="completed",
        enhanced_data=enhanced_data,
        pending_enhanced_data=None,
        completion_message=COMPLETION_MESSAGE
    )
    return EnhancementResponse(
        success=True,
        session_id=session_id,
        enhanced_data=enhanced_data,
        ai_message=COMPLETION_MESSAGE,
        is_complete=True,
        week_number=session["week_number"],
        user_id=session["user_id"]
    )


async def run_turn(session_id: str, history: list) -> EnhancementResponse:
    """Send the full conversation to Claude and build the response."""
    session = session_manager.get_session(session_id)

    try:
        report_data = _report_data_from_dict(session.get("report_data") or {})
    except Exception as e:
        return EnhancementResponse(
            success=False,
            session_id=session_id,
            error=f"Invalid session report data: {e}"
        )

    result = claude_service.converse(report_data, history)

    if not result["success"]:
        # Leave the stored history untouched so the client can retry with the
        # same answer without breaking the alternating user/assistant structure.
        return EnhancementResponse(
            success=False,
            session_id=session_id,
            error=result["error"]
        )

    if result["tokens_used"]:
        analytics_tracker.log_usage(session["user_id"], result["tokens_used"])

    history = history + [{"role": "assistant", "content": result["raw"]}]

    if result["type"] == "question":
        session_manager.update_session(
            session_id,
            conversation_history=history,
            status="in_progress",
            pending_enhanced_data=None
        )
        return EnhancementResponse(
            success=True,
            session_id=session_id,
            question=result["question"],
            ai_message=result["ai_message"],
            is_complete=False,
            ready_to_enhance=False,
            suggestions=result.get("suggestions"),
            tokens_used=result["tokens_used"],
            week_number=session["week_number"],
            user_id=session["user_id"]
        )

    # "ready": enough data gathered, the draft waits for the user's go-ahead.
    session_manager.update_session(
        session_id,
        conversation_history=history,
        status="ready",
        pending_enhanced_data=result["enhanced_data"]
    )
    return EnhancementResponse(
        success=True,
        session_id=session_id,
        question=None,
        ai_message=result["confirmation_message"],
        is_complete=False,
        ready_to_enhance=True,
        suggestions=result.get("suggestions"),
        tokens_used=result["tokens_used"],
        week_number=session["week_number"],
        user_id=session["user_id"]
    )


async def handle_enhancement(session_id: str, request: EnhancementRequest) -> EnhancementResponse:
    """Handle direct enhancement mode (non-conversational)."""
    session = session_manager.get_session(session_id)
    try:
        report_data = request.report_data or _report_data_from_dict(session.get("report_data") or {})
    except Exception as e:
        return EnhancementResponse(
            success=False,
            session_id=session_id,
            error=f"Invalid session report data: {e}"
        )
    instructions = report_data.additional_instructions or ""

    result = claude_service.enhance_weekly_report(report_data, instructions)

    if result["success"]:
        analytics_tracker.log_usage(session["user_id"], result["tokens_used"])
        session_manager.update_session(
            session_id,
            status="completed",
            enhanced_data=result["enhanced_data"]
        )
        return EnhancementResponse(
            success=True,
            session_id=session_id,
            enhanced_data=result["enhanced_data"],
            ai_message=COMPLETION_MESSAGE,
            tokens_used=result["tokens_used"],
            is_complete=True,
            week_number=session["week_number"],
            user_id=session["user_id"]
        )

    session_manager.update_session(session_id, status="failed")
    return EnhancementResponse(
        success=False,
        session_id=session_id,
        error=result["error"]
    )


def _report_data_from_dict(report_data: dict):
    """Rebuild a WeeklyReportData from the stored session payload."""
    from app.schemas import WeeklyReportData
    return WeeklyReportData(**(report_data or {}))


def pending_response(session_id: str, session: dict) -> EnhancementResponse:
    """Replay the last Claude turn without spending another API call."""
    from app.ai import _parse_json

    base = {
        "session_id": session_id,
        "week_number": session["week_number"],
        "user_id": session["user_id"]
    }

    if session.get("status") == "completed" and session.get("enhanced_data"):
        return EnhancementResponse(
            success=True,
            enhanced_data=session["enhanced_data"],
            ai_message=session.get("completion_message", COMPLETION_MESSAGE),
            is_complete=True,
            **base
        )

    history = session.get("conversation_history") or []
    last_assistant = next(
        (m["content"] for m in reversed(history) if m.get("role") == "assistant"),
        None
    )
    parsed = None
    if last_assistant:
        try:
            parsed = _parse_json(last_assistant)
        except ValueError:
            parsed = None

    if parsed and parsed.get("type") == "complete":
        return EnhancementResponse(
            success=True,
            enhanced_data=parsed.get("enhanced_data") or {},
            ai_message=session.get("completion_message", COMPLETION_MESSAGE),
            is_complete=True,
            **base
        )

    if parsed and parsed.get("type") == "ready":
        confirmation = (parsed.get("confirmation_message") or "").strip()
        return EnhancementResponse(
            success=True,
            ai_message=confirmation or READY_FALLBACK_MESSAGE,
            is_complete=False,
            ready_to_enhance=True,
            suggestions=_clean_suggestions(parsed.get("suggestions")),
            **base
        )

    if parsed and parsed.get("type") == "question":
        question = (parsed.get("question") or "").strip()
        return EnhancementResponse(
            success=True,
            question=question,
            ai_message=(parsed.get("ai_message") or question),
            is_complete=False,
            ready_to_enhance=False,
            suggestions=_clean_suggestions(parsed.get("suggestions")),
            **base
        )

    draft = session.get("pending_enhanced_data")
    if session.get("status") == "ready" and isinstance(draft, dict) and draft:
        return EnhancementResponse(
            success=True,
            ai_message=READY_FALLBACK_MESSAGE,
            is_complete=False,
            ready_to_enhance=True,
            **base
        )

    return EnhancementResponse(
        success=False,
        error="Session has no pending question",
        **base
    )


@router.get("/health")
async def health_check():
    return {"status": "healthy", "service": "mipt-ai-service"}
