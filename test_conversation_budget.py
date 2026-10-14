"""Chat calls stay small, and the opening is a concept rather than one script."""
import json
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai import (
    CONVERSATION_RULES,
    compact_assistant,
    history_for_model,
    question_from_prose,
    visible_history,
)


def test_rules_name_mr_pt_without_a_fixed_script():
    assert "Mr PT" in CONVERSATION_RULES
    assert "personal Supervisor" in CONVERSATION_RULES
    assert "your own words" in CONVERSATION_RULES
    assert "Do not reuse a fixed script" in CONVERSATION_RULES
    # The repeated instructions used to be a long script. Keep them short.
    assert len(CONVERSATION_RULES) < 2500


def test_ready_json_is_not_sent_back_to_the_model():
    raw = json.dumps({
        "type": "ready",
        "confirmation_message": "I can enhance this now.",
        "enhanced_data": {"daily_reports": [{"description": "x" * 2000}]},
    })
    compact = compact_assistant(raw)
    assert compact == "I can enhance this now."
    assert "x" * 50 not in compact
    assert len(compact) < len(raw) / 10


def test_older_answers_are_folded_and_the_draft_is_dropped():
    history = []
    for i in range(8):
        history.append({"role": "user", "content": f"On day {i} I wired the panel."})
        history.append({
            "role": "assistant",
            "content": json.dumps({
                "type": "question",
                "ai_message": "Thanks.",
                "question": f"{i}. What next?",
                "enhanced_data": {"blob": "y" * 1500},
            }),
        })

    sent = history_for_model(history)
    blob = json.dumps(sent)
    assert "yyyy" not in blob
    assert "Earlier student answers" in sent[0]["content"]
    assert "wired the panel" in sent[0]["content"]
    assert sent[0]["role"] == "user"
    assert len(blob) < len(json.dumps(history)) / 5


def test_browsable_chat_hides_model_json_and_the_opener():
    session = {
        "conversation_history": [
            {"role": "user", "content": "I just opened my report for this week."},
            {"role": "assistant", "content": json.dumps({
                "type": "question",
                "ai_message": "I'm Mr PT, your personal Supervisor for this milling week.",
                "question": "1. What did you do on Monday?",
                "enhanced_data": {"blob": "z" * 500},
            })},
            {"role": "user", "content": "I faced a bracket on the mill."},
        ]
    }
    shown = visible_history(session)
    assert shown[0]["role"] == "assistant"
    assert "Mr PT" in shown[0]["content"]
    assert "Monday" in shown[0]["content"]
    assert "zzzz" not in shown[0]["content"]
    assert shown[1] == {"role": "user", "content": "I faced a bracket on the mill."}


def test_browsable_chat_keeps_the_full_reply():
    long_answer = "Fluke makes several earthing testers. " + ("Common models include the 1625. " * 40)
    session = {
        "conversation_history": [
            {"role": "assistant", "content": json.dumps({
                "type": "question",
                "ai_message": long_answer,
                "question": "1. Which exact model did you use?",
            })}
        ]
    }
    shown = visible_history(session)[0]["content"]
    assert shown.startswith("Fluke makes several earthing testers.")
    assert shown.count("Common models include the 1625.") == 40
    assert "Which exact model did you use?" in shown
    assert len(shown) > 500


def test_plain_explanation_becomes_a_chat_reply():
    prose = (
        "Let me explain each question so you can answer accurately:\n\n"
        "**Question 1 - Which specific Fluke instruments:**\n"
        "Fluke makes several earthing testers. I need the exact model you used."
    )
    recovered = question_from_prose(prose)
    shown = recovered["ai_message"] + "\n" + recovered["question"]
    assert "Could not parse" not in shown
    assert "Fluke makes several earthing testers" in shown
    assert "**" not in shown
    stored = json.loads(recovered["raw"])
    assert stored["type"] == "question"
