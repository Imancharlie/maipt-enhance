import anthropic
from app.config import settings
from app.schemas import WeeklyReportData
import json
import re

# Strips markdown code fences (``` / ```json) wherever they appear in a response.
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+\-]*\s*|\s*```")

READY_FALLBACK_MESSAGE = (
    "I now have enough information to enhance your report without guessing. "
    "Would you like me to go ahead and enhance it?"
)

MAX_SUGGESTIONS = 5
MAX_DIAGRAM_SUGGESTIONS = 3
MAX_DAILY_DESCRIPTION_CHARS = 500
# Recent turns sent to Claude. Older student answers are folded into one short note.
MAX_RECENT_MESSAGES = 10

# Static instructions. Kept short because they are sent on every turn.
CONVERSATION_RULES = """You are Mr PT, the student's personal Supervisor for university practical training (PT). Guide this weekly report with them, prepare it together, and remind them of important points about their PT. If they ask you to clarify a machine, tool, or step, explain it so they can implement it.

Reply with one JSON object only. No markdown.

Still gathering facts:
{"type":"question","ai_message":"...","question":"1. ...\\n2. ..."}
Add "suggestions" only for a real choice of 2-4 short answers. Otherwise omit it.

They ask to enhance while facts are missing: stay on type question, name what is missing, suggestions ["Enhance with what you have","Provide missing details"].
They then agree to enhance with what you have: type ready, using only facts you actually have.

Enough real facts to write the report:
{"type":"ready","confirmation_message":"...","diagram_suggestions":["You may attach ..."],"enhanced_data":{"main_job_title":"","daily_reports":[{"day":"","date":"","description":"","hours_worked":0}],"operations":[{"step_number":1,"operation_description":"","tools_used":""}]}}
diagram_suggestions: exactly 3 short lines for this main job. Do not put them in the report text. Never use type complete.

Facts: use only REPORT and what the student said. Never invent hours, tools, days, or results.
First reply (no earlier reply from you): ai_message is a natural introduction in your own words. Do not reuse a fixed script. Always the same points, fitted to what they just said and to this week's job or company: you are Mr PT, their personal Supervisor; you will go through this week's report with them and prepare it together; you remind them of important things about their PT; they can ask you to clarify something so they can implement it. Then put the first questions in question.
Later replies: do not introduce yourself again. ai_message is one short sentence. If they asked you to clarify something, ai_message is that short reminder, and question connects it to the report (confirm the tool, the step, or the next missing fact). The report still moves forward the same way.
question: 1-3 numbered lines. Never re-ask a known fact.
enhanced_data: same days and dates as REPORT. hours_worked is their number, or 0 only if they were off. Each daily description 300-500 characters. 4-6 operations with only step_number, operation_description, tools_used. Keep their job title.
"""

_SYNTHETIC_USER_PREFIXES = (
    "please start enhancing",
    "i just opened my report",
    "please continue enhancing",
)


def _clean_suggestions(raw) -> list:
    """Keep only a short list of non-empty strings for quick reply chips."""
    if not isinstance(raw, list):
        return None
    items = [str(s).strip() for s in raw if isinstance(s, (str, int, float))]
    items = [s for s in items if s][:MAX_SUGGESTIONS]
    return items or None


def _clean_diagram_suggestions(raw) -> list:
    """Keep a few short attachment ideas. Local display only, no extra model call."""
    if not isinstance(raw, list):
        return None
    items = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if text:
            items.append(text[:120])
        if len(items) == MAX_DIAGRAM_SUGGESTIONS:
            break
    return items or None


def attach_diagram_note(message: str, suggestions) -> str:
    """Append the attachment ideas to the message the student already sees."""
    items = _clean_diagram_suggestions(suggestions)
    if not items:
        return message
    lines = "\n".join(f"- {item}" for item in items)
    return f"{(message or '').rstrip()}\n\nYou may attach:\n{lines}"


def _is_synthetic_user(text: str) -> bool:
    lowered = " ".join((text or "").lower().split())
    return any(lowered.startswith(prefix) for prefix in _SYNTHETIC_USER_PREFIXES)


def compact_assistant(content: str) -> str:
    """Drop stored JSON (especially drafted reports) before the next model call."""
    try:
        parsed = _parse_json(content)
    except ValueError:
        return " ".join((content or "").split())[:400]

    kind = parsed.get("type")
    if kind == "question":
        ai_message = (parsed.get("ai_message") or "").strip()
        question = (parsed.get("question") or "").strip()
        return f"{ai_message}\n{question}".strip()[:500]
    if kind in ("ready", "complete"):
        return ((parsed.get("confirmation_message") or "Draft ready.").strip())[:300]
    return " ".join((content or "").split())[:400]


def history_for_model(conversation_history: list) -> list:
    """Short transcript for Claude. The full chat stays stored for the student."""
    compact = []
    for message in conversation_history or []:
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        if role == "assistant":
            content = compact_assistant(content)
        else:
            content = content.strip()
        if content:
            compact.append({"role": role, "content": content})

    if len(compact) <= MAX_RECENT_MESSAGES:
        return _ensure_user_first(compact)

    recent = compact[-MAX_RECENT_MESSAGES:]
    if recent[0]["role"] != "user":
        recent = compact[-(MAX_RECENT_MESSAGES + 1):]
    older = compact[:len(compact) - len(recent)]
    facts = []
    for message in older:
        if message["role"] != "user" or _is_synthetic_user(message["content"]):
            continue
        facts.append(" ".join(message["content"].split())[:160])
    if facts:
        note = "Earlier student answers (do not ask again): " + " | ".join(facts)
        if len(note) > 900:
            note = note[:900].rstrip()
        if recent[0]["role"] == "user":
            recent[0] = {
                "role": "user",
                "content": note + "\n\n" + recent[0]["content"],
            }
        else:
            recent = [{"role": "user", "content": note}] + recent
    return _ensure_user_first(recent)


def visible_history(session: dict) -> list:
    """The chat as the student saw it, without stored model JSON."""
    items = []
    for message in (session or {}).get("conversation_history") or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        if role == "assistant":
            content = compact_assistant(content)
        elif _is_synthetic_user(content):
            continue
        content = content.strip()
        if content:
            items.append({"role": role, "content": content})
    return items


def _ensure_user_first(messages: list) -> list:
    if messages and messages[0]["role"] != "user":
        return [{"role": "user", "content": "Continue this week's report."}] + messages
    return messages


def _parse_json(text: str) -> dict:
    """Parse a JSON object out of a Claude response.

    Claude sometimes wraps valid JSON in markdown code fences or prefixes it
    with prose, so every response goes through the same tolerant extraction.
    Raises ValueError when no JSON object can be recovered.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty response from Claude")

    cleaned = _FENCE_RE.sub("", text).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start:end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from Claude response: {text[:500]}")


def _enforce_description_limits(enhanced_data: dict) -> dict:
    """Ensure daily descriptions don't exceed MAX_DAILY_DESCRIPTION_CHARS (500)."""
    if not isinstance(enhanced_data, dict):
        return enhanced_data

    daily_reports = enhanced_data.get("daily_reports")
    if isinstance(daily_reports, list):
        for report in daily_reports:
            if isinstance(report, dict):
                description = report.get("description", "")
                if isinstance(description, str) and len(description) > MAX_DAILY_DESCRIPTION_CHARS:
                    # Truncate to max limit at a word boundary
                    truncated = description[:MAX_DAILY_DESCRIPTION_CHARS]
                    # Find last space to avoid cutting mid-word
                    last_space = truncated.rfind(" ")
                    if last_space > MAX_DAILY_DESCRIPTION_CHARS * 0.8:  # Only if we're not cutting too much
                        truncated = truncated[:last_space]
                    report["description"] = truncated.rstrip()

    return enhanced_data


class ClaudeService:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def _create_message(self, system: str, messages: list) -> tuple:
        """Call Claude and return (text, tokens_used)."""
        response = self.client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=settings.MAX_TOKENS,
            temperature=settings.TEMPERATURE,
            system=system,
            messages=messages
        )

        text = "".join(
            block.text for block in response.content
            if getattr(block, "type", None) == "text"
        )
        if not text.strip():
            raise ValueError("Empty or invalid response from Claude API")
        if getattr(response, "stop_reason", None) == "max_tokens":
            # Never hand back a half-written answer: say so instead.
            raise ValueError(
                "Claude's reply hit the output limit and was cut off. "
                "Increase MAX_TOKENS and try again."
            )

        return text, response.usage.input_tokens + response.usage.output_tokens

    def enhance_weekly_report(self, data: WeeklyReportData, instructions: str = "") -> dict:
        """Single-shot (non-conversational) enhancement."""
        try:
            prompt = self._create_enhancement_prompt(data, instructions)
            text, tokens_used = self._create_message(
                "Return only valid JSON, no markdown.",
                [{"role": "user", "content": prompt}]
            )
            enhanced_data = _parse_json(text)
            enhanced_data = _enforce_description_limits(enhanced_data)

            return {
                "success": True,
                "enhanced_data": enhanced_data,
                "tokens_used": tokens_used
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def converse(self, data: WeeklyReportData, conversation_history: list) -> dict:
        """Multi-turn enhancement.

        Sends the full conversation history to Claude which must answer with
        strict JSON: a question to ask the user, or (once everything is known)
        a "ready" response carrying the drafted report that still needs the
        user's go-ahead before it is saved.
        """
        try:
            system = self._create_conversation_system_prompt(data)

            messages = history_for_model(conversation_history)
            if not messages:
                messages = [{"role": "user", "content": "I just opened my report for this week."}]

            last_user_msg = None
            for message in reversed(messages):
                if message.get("role") == "user":
                    last_user_msg = message.get("content", "").lower()
                    break

            if last_user_msg and ("enhance with what you have" in last_user_msg or
                                   "enhance my report with the information available" in last_user_msg):
                system += "\nReply type ready now, using only the facts you have."

            text, tokens_used = self._create_message(system, messages)
            parsed = _parse_json(text)

            response_type = parsed.get("type")
            if response_type in ("complete", "ready"):
                # The chat must always wait for the user's go-ahead, so a
                # "complete" from Claude is downgraded to a draft ("ready").
                enhanced_data = parsed.get("enhanced_data")
                if not isinstance(enhanced_data, dict):
                    raise ValueError("Ready response is missing enhanced_data")
                enhanced_data = _enforce_description_limits(enhanced_data)
                confirmation = (parsed.get("confirmation_message") or "").strip()
                return {
                    "success": True,
                    "type": "ready",
                    "enhanced_data": enhanced_data,
                    "confirmation_message": confirmation or READY_FALLBACK_MESSAGE,
                    "diagram_suggestions": _clean_diagram_suggestions(
                        parsed.get("diagram_suggestions")
                    ),
                    "raw": text,
                    "tokens_used": tokens_used
                }
            elif response_type == "question":
                question = (parsed.get("question") or "").strip()
                if not question:
                    raise ValueError("Question response is missing the question")
                ai_message = (parsed.get("ai_message") or "").strip() or question
                return {
                    "success": True,
                    "type": "question",
                    "question": question,
                    "ai_message": ai_message,
                    "suggestions": _clean_suggestions(parsed.get("suggestions")),
                    "raw": text,
                    "tokens_used": tokens_used
                }
            else:
                raise ValueError(f"Unexpected response type: {response_type!r}")
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _render_report_data(self, data: WeeklyReportData) -> str:
        lines = [
            f"Program: {data.user_program or '-'}",
            f"Company: {data.company_name or '-'}",
            f"Week: {data.week_number}",
            f"Main job: {data.main_job_title or '-'}",
            "Days:"
        ]
        empty_days = []
        unclear_days = []
        for day in data.daily_reports:
            description = (day.get('description') or '').strip()
            hours = day.get('hours_worked')
            if description:
                desc_text = description
            else:
                desc_text = "empty"
                empty_days.append(str(day.get('day')))
            if description and len(description.split()) <= 6:
                unclear_days.append(str(day.get('day')))
            hours_text = "no hours" if hours in (None, "") else f"{hours}h"
            lines.append(f"- {day.get('day')} ({day.get('date')}): {desc_text} [{hours_text}]")

        if not data.daily_reports:
            lines.append("- empty")

        lines.append("Operations:")
        if data.operations:
            for op in data.operations:
                lines.append(
                    f"{op.get('step_number')}. {op.get('operation_description')} "
                    f"(tools: {op.get('tools_used')})"
                )
        else:
            lines.append("- none")

        if empty_days:
            lines.append("Missing days: " + ", ".join(empty_days))
        if unclear_days:
            lines.append("Vague days: " + ", ".join(unclear_days))

        if data.additional_instructions:
            extra = " ".join(data.additional_instructions.split())
            if len(extra) > 400:
                extra = extra[:400].rstrip() + "..."
            lines.append(f"Notes: {extra}")

        return "\n".join(lines)

    def _create_enhancement_prompt(self, data: WeeklyReportData, instructions: str) -> str:
        prompt = f"Enhance this weekly PT report. Keep the days. Daily descriptions 300-500 characters. 4-6 operations.\n\n{self._render_report_data(data)}\n"
        if instructions:
            extra = " ".join(instructions.split())
            if len(extra) > 400:
                extra = extra[:400].rstrip() + "..."
            prompt += f"\nNotes: {extra}\n"
        prompt += (
            '\nReturn JSON only: {"main_job_title":"","daily_reports":'
            '[{"day":"","description":"","hours_worked":0}],'
            '"operations":[{"step_number":1,"operation_description":"","tools_used":""}]}'
        )
        return prompt

    def _create_conversation_system_prompt(self, data: WeeklyReportData) -> str:
        return f"{CONVERSATION_RULES}\nREPORT:\n{self._render_report_data(data)}"


claude_service = ClaudeService()
