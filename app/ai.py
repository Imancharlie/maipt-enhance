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
MAX_DAILY_DESCRIPTION_CHARS = 500


def _clean_suggestions(raw) -> list:
    """Keep only a short list of non-empty strings for quick reply chips."""
    if not isinstance(raw, list):
        return None
    items = [str(s).strip() for s in raw if isinstance(s, (str, int, float))]
    items = [s for s in items if s][:MAX_SUGGESTIONS]
    return items or None


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
                "You are a report enhancement assistant. Return only valid JSON, "
                "never markdown code fences.",
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

            messages = [
                {"role": m["role"], "content": m["content"]}
                for m in conversation_history
                if m.get("role") in ("user", "assistant") and m.get("content")
            ]
            if not messages:
                messages = [{"role": "user", "content": "Please start enhancing my weekly report."}]

            # Check if the last user message is a request to enhance with available data
            last_user_msg = None
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user_msg = m.get("content", "").lower()
                    break

            # If user explicitly requests enhancement with available data, add instruction to system
            if last_user_msg and ("enhance with what you have" in last_user_msg or
                                   "enhance my report with the information available" in last_user_msg):
                system += "\n\nIMPORTANT: The user has requested to enhance with available information. Switch to 'ready' type and provide enhanced_data using whatever information you have, even if incomplete."

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
            f"Student Program: {data.user_program or 'Not specified'}",
            f"Company: {data.company_name or 'Not specified'}",
            f"Week Number: {data.week_number}",
            f"Main Job Title: {data.main_job_title or 'Not specified'}",
            "",
            "Daily Reports:"
        ]
        empty_days = []
        unclear_days = []
        for day in data.daily_reports:
            description = (day.get('description') or '').strip()
            hours = day.get('hours_worked')
            if description:
                desc_text = description
            else:
                desc_text = "NOTHING RECORDED YET - ask the user what they did this day"
                empty_days.append(str(day.get('day')))
            if description and len(description.split()) <= 6:
                unclear_days.append(str(day.get('day')))
            if hours in (None, ''):
                hours_text = "hours NOT recorded - ask the user"
            else:
                hours_text = f"{hours} hours"
            lines.append(f"- {day.get('day')} ({day.get('date')}): {desc_text} [{hours_text}]")

        if not data.daily_reports:
            lines.append("- NO DAILY ENTRIES AT ALL - the report is empty")

        lines.append("")
        lines.append("Main Job Operations:")
        if data.operations:
            for op in data.operations:
                lines.append(
                    f"Step {op.get('step_number')}: {op.get('operation_description')} "
                    f"(Tools: {op.get('tools_used')})"
                )
        else:
            lines.append("NO OPERATIONS RECORDED - ask the user about the steps they followed")

        if empty_days:
            lines.append("")
            lines.append(
                "Days with no data (you MUST ask the user about these before enhancing): "
                + ", ".join(empty_days)
            )
        if unclear_days:
            lines.append(
                "Days with very short/vague descriptions worth expanding: "
                + ", ".join(unclear_days)
            )

        if data.additional_instructions:
            lines.append("")
            lines.append(f"Additional Instructions: {data.additional_instructions}")

        return "\n".join(lines)

    def _create_enhancement_prompt(self, data: WeeklyReportData, instructions: str) -> str:
        prompt = f"""
You are enhancing a weekly report for industrial training.

{self._render_report_data(data)}
"""
        if instructions:
            prompt += f"\nAdditional Instructions: {instructions}\n"

        prompt += """
Enhance this report by:
1. Improving descriptions to be more professional and technical (STRICT LIMIT: 300-500 characters per daily description, NEVER exceed 500 characters)
2. Adding relevant technical details where appropriate
3. Improving operation descriptions to be more specific
4. Maintaining the original structure

Return ONLY valid JSON in this format:
{
    "daily_reports": [
        {"day": "Monday", "description": "enhanced description", "hours_worked": 8.0},
        ...
    ],
    "operations": [
        {"step_number": 1, "operation_description": "enhanced", "tools_used": "enhanced"},
        ...
    ],
    "main_job_title": "enhanced title"
}
"""
        return prompt

    def _create_conversation_system_prompt(self, data: WeeklyReportData) -> str:
        return f"""You are helping a student enhance their weekly industrial training report through a chat.

REPORT DATA:
{self._render_report_data(data)}

YOUR JOB
1. Find everything missing or unclear in the report: empty days, missing hours, vague descriptions,
   outcomes and results, tools and equipment used, tests or checks done, problems solved, feedback or
   ratings from the supervisor, and the main job steps.
2. Collect it from the user with short questions. Never invent, guess, or assume anything - only use
   facts the user told you or facts already present in REPORT DATA above.
3. As soon as you can write the whole report from real information only, say that you have enough
   information to enhance the report without guessing, and WAIT for the user's go-ahead.

QUESTION STYLE
- Ask 1 to 3 questions in one turn when several things are missing; keep the turn short.
- Number the questions so they are easy to answer: each question on its own line starting with
  "1.", then "2.", then "3." (use a real line break between them).
- In "ai_message" write only one short friendly sentence.
- "suggestions" is OPTIONAL and off by default: include it ONLY when the question is a genuine
  closed choice with 2-4 short, tappable answers (for example: day off / worked / no hours recorded,
  yes / no). For open questions such as "what did you do on Wednesday?" or "how many hours did you
  work?" leave "suggestions" out entirely. Never add suggestions to a "ready" response.
- If REPORT DATA is empty, build the whole report through the chat: ask the user what they did on
  each day of the week (Monday to Friday), then about the main job, the steps and the tools used.
- Never re-ask about something you already know from REPORT DATA or from earlier answers.
- Stay on topic: only ask things that improve this specific weekly report.

HANDLING PREMATURE ENHANCEMENT REQUESTS
- If the user asks to "enhance the report", "prepare the report", "create the report", or similar
  while you still have questions, acknowledge their request but explain what information is still
  missing.
- Then offer them the option to enhance with what you have: "Would you like me to enhance with the
  information available, or provide the missing details first?"
- For such cases, include "suggestions": ["Enhance with what you have", "Provide missing details"]
- DO NOT switch to "ready" type yet - stay in "question" type but with this specific question and
  the two suggestions above.
- If the user then says "Enhance with what you have" or "Please enhance my report with the information
  available", switch to "ready" type and provide the enhanced_data using whatever information you have,
  even if some details are missing. Mark this in your confirmation message.

RESPONSE FORMAT - STRICT JSON ONLY, no markdown fences, no commentary, exactly one of:

If you still need information:
{{"type":"question","question":"<the questions, numbered, each on its own line, e.g. 1. What did you do on Wednesday?<newline>2. How many hours did you work?>","ai_message":"<one short friendly sentence>"}}

Only for a real multiple-choice question, add suggestions as well:
{{"type":"question","question":"...","ai_message":"...","suggestions":["<short option 1>","<short option 2>"]}}

When the user requests enhancement prematurely (missing info):
{{"type":"question","question":"I still need some information to complete your report: <list what's missing>. Would you like me to enhance with what's available, or provide the missing details first?","ai_message":"<one short friendly sentence>","suggestions":["Enhance with what you have","Provide missing details"]}}

When the user confirms "Enhance with what you have" or similar (even with missing info):
{{"type":"ready","confirmation_message":"I'll enhance your report with the information available. Some details may be marked as incomplete. Please review and you can always provide more details later.","enhanced_data":{{"main_job_title":"<enhanced title>","daily_reports":[{{"day":"Monday","date":"<keep the original date>","description":"<enhanced description, professional and technical, STRICT LIMIT 300-500 characters>","hours_worked":8.0}}],"operations":[{{"step_number":1,"operation_description":"<enhanced step>","tools_used":"<tools used>"}}]}}}}

When you have everything you need (and NOT before):
{{"type":"ready","confirmation_message":"<plainly say you have enough information to enhance the report without guessing, then ask for the user's go-ahead>","enhanced_data":{{"main_job_title":"<enhanced title>","daily_reports":[{{"day":"Monday","date":"<keep the original date>","description":"<enhanced description, professional and technical, STRICT LIMIT 300-500 characters>","hours_worked":8.0}}],"operations":[{{"step_number":1,"operation_description":"<enhanced step>","tools_used":"<tools used>"}}]}}}}

NEVER return "complete" while chatting: the report is only saved after the user presses the
enhance button.

RULES FOR enhanced_data:
- Keep the SAME days and dates that appear in REPORT DATA; do not add or remove days.
- hours_worked must be a number: keep the hours the user gave, and use 0 only when the user said
  the day was off or they were absent. Never make up hours.
- Enhance descriptions to be professional and technical (STRICT LIMIT: 300-500 characters per daily description, NEVER exceed 500 characters). Count characters carefully and stay within limit.
- Provide 4-6 operations covering the main job systematically and sequentially.
- Each operation object must contain ONLY: step_number, operation_description, tools_used.
- main_job_title: keep the user's title if provided (make it more specific if useful),
  otherwise suggest one based on the daily work.
"""


claude_service = ClaudeService()
