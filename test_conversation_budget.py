"""Chat calls stay small, and the opening is a concept rather than one script."""
import json
import os

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai import CONVERSATION_RULES, compact_assistant, history_for_model


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
