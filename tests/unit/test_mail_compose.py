import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import input_values
from goal_tracker.llm import gateway as gateway_module
from goal_tracker.llm.schemas import DailyMailDraft
from goal_tracker.mail.compose import (
    MAIL_SECTIONS,
    answer_bullets,
    answers_as_journal,
    merge_written_sections,
    section_placeholders,
)
from goal_tracker.mail.daily import PLACEHOLDERS

ANSWERS = {
    "meetings_activities": "standup\n- sprint planning",
    "use_cases_completed": "finished pdf export; started mail feature",
    "learning": "",
    "plan_for_tomorrow": "write tests",
    "blockers": "",
}


# ------------------------------------------------------------- pure logic


def test_sections_fill_known_placeholders():
    assert {s.placeholder for s in MAIL_SECTIONS} <= set(PLACEHOLDERS)
    assert {s.field for s in MAIL_SECTIONS} == set(DailyMailDraft.model_fields)


def test_answer_bullets_split_lines_and_semicolons():
    assert answer_bullets(" - one;two\n\n* three\n1. four ") == [
        "one", "two", "three", "four",
    ]


def test_model_output_used_only_for_answered_sections():
    written = {
        "meetings_activities": ["Attended the daily stand-up", "Joined sprint planning"],
        "use_cases_completed": [],  # model returned nothing -> own words
        "learning": ["Invented learning"],  # user left it empty -> dropped
        "plan_for_tomorrow": ["Write tests for the mail feature"],
        "blockers": ["Invented blocker"],
    }

    merged = merge_written_sections(ANSWERS, written)

    assert merged == {
        "meetings_activities": ["Attended the daily stand-up", "Joined sprint planning"],
        "use_cases_completed": ["finished pdf export", "started mail feature"],
        "learning": [],
        "plan_for_tomorrow": ["Write tests for the mail feature"],
        "blockers": [],
    }


def test_model_bullets_with_several_sentences_are_split():
    written = {
        "meetings_activities": [
            "Attended standup with Arpan. Participated in sprint planning.",
            "Reviewed v2.0 of the API docs.",
        ]
    }

    result = merge_written_sections({"meetings_activities": "standup"}, written)

    assert result["meetings_activities"] == [
        "Attended standup with Arpan.",
        "Participated in sprint planning.",
        "Reviewed v2.0 of the API docs.",
    ]


def test_without_model_the_users_words_are_used():
    merged = merge_written_sections(ANSWERS, None)

    assert merged["meetings_activities"] == ["standup", "sprint planning"]
    assert section_placeholders(merged) == {
        "other_tasks": "- standup\n- sprint planning",
        "goal_tasks": "- finished pdf export\n- started mail feature",
        "learnings": "- None",
        "all_open_tasks": "- write tests",
        "challenges": "- None",
    }


def test_answers_become_journal_text_for_progress():
    assert answers_as_journal(ANSWERS) == (
        "Meetings / Activities:\nstandup\n- sprint planning\n\n"
        "Use Cases / Activities Completed:\nfinished pdf export; started mail feature\n\n"
        "Plan for Tomorrow:\nwrite tests"
    )


# --------------------------------------------------------------- web flow


def _body(html: str) -> str:
    start = html.index('<textarea id="mail-body"')
    return html[html.index(">", start) + 1: html.index("</textarea>", start)]


def test_compose_form_asks_one_question_per_section(web):
    page = web.client.get("/mail")

    assert page.status_code == 200
    for section in MAIL_SECTIONS:
        assert f'name="{section.field}"' in page.text
        assert section.question in page.text


def test_model_writes_mail_in_template_format(web):
    web.gateway.mail_draft = DailyMailDraft(
        meetings_activities=["Attended the daily stand-up"],
        use_cases_completed=["Completed the PDF export", "Started the mail feature"],
        learning=["Should be dropped"],
        plan_for_tomorrow=["Write tests"],
        blockers=[],
    )

    page = web.client.post("/mail", data={"on": web.today.isoformat(), **ANSWERS})
    body = _body(page.text)

    assert page.status_code == 200
    assert web.gateway.mail_answers["learning"] == ""
    assert body.startswith("Hi Arpan,")
    assert "1. Meetings / Activities\n- Attended the daily stand-up\n" in body
    assert (
        "2. Use Cases / Activities Completed\n"
        "- Completed the PDF export\n- Started the mail feature\n"
    ) in body
    assert "3. Learning\n- None\n" in body
    assert "4. Plan for Tomorrow\n- Write tests\n" in body
    assert "5. Blockers / Support Required\n- None\n" in body
    assert body.rstrip().endswith("Regards,\nAtharv")
    assert "Daily Update – " in page.text
    assert "rephrased by the local model" in page.text

    # Offer to save the same answers as progress through the AI review flow.
    assert input_values(page.text, "method") == ["ai"]
    assert input_values(page.text, "raw_text")[0].startswith("Meetings / Activities:")


def test_model_failure_uses_answers_as_written(web):
    web.gateway.error = ConnectionError("offline")

    page = web.client.post("/mail", data={"on": web.today.isoformat(), **ANSWERS})

    assert page.status_code == 200
    assert "your answers were used as written" in page.text
    assert "1. Meetings / Activities\n- standup\n- sprint planning\n" in _body(page.text)


def test_blank_answers_are_rejected(web):
    page = web.client.post("/mail", data={"on": web.today.isoformat()})

    assert page.status_code == 422
    assert "Answer at least one question" in page.text
    assert web.gateway.calls == []


def test_edit_returns_answers_to_the_form(web):
    page = web.client.post(
        "/mail",
        data={"on": "2026-01-05", "action": "edit", **ANSWERS},
    )

    assert page.status_code == 200
    assert "finished pdf export; started mail feature</textarea>" in page.text
    assert 'value="2026-01-05"' in page.text
    assert web.gateway.calls == []


def test_future_day_falls_back_to_today(web):
    page = web.client.get(f"/mail?on={date(2999, 1, 1).isoformat()}")

    assert "showing today" in page.text
    assert f'value="{web.today.isoformat()}"' in page.text


# ---------------------------------------------------------------- gateway


@pytest.fixture
def simulated_gateway(monkeypatch):
    model = Mock()
    writer = Mock()
    model.with_structured_output.return_value = writer
    monkeypatch.setattr(gateway_module, "create_model", lambda settings: model)
    gateway = gateway_module.ModelGateway(
        SimpleNamespace(llm_structured_method="json_schema")
    )
    return gateway, writer


def test_gateway_sends_only_answered_sections(simulated_gateway):
    gateway, writer = simulated_gateway
    draft = DailyMailDraft(
        meetings_activities=[],
        use_cases_completed=[],
        learning=[],
        plan_for_tomorrow=["Write tests"],
        blockers=[],
    )
    writer.invoke.return_value = {"parsing_error": None, "parsed": draft}

    result = gateway.write_daily_mail({**ANSWERS, "unexpected": "ignore me"})

    assert result == draft
    system, human = writer.invoke.call_args.args[0]
    assert "Daily Mail Writing" in system[1]
    assert json.loads(human[1]) == {
        "meetings_activities": "standup\n- sprint planning",
        "plan_for_tomorrow": "write tests",
        "use_cases_completed": "finished pdf export; started mail feature",
    }


def test_gateway_rejects_empty_answers_and_bad_output(simulated_gateway):
    gateway, writer = simulated_gateway

    with pytest.raises(ValueError, match="At least one answer"):
        gateway.write_daily_mail({"learning": "  "})
    writer.invoke.assert_not_called()

    writer.invoke.return_value = {"parsing_error": ValueError(), "parsed": None}
    with pytest.raises(ValueError, match="invalid mail structure"):
        gateway.write_daily_mail(ANSWERS)


def test_every_mail_section_is_required_in_the_schema():
    # With optional fields the local 4B model tended to return all sections
    # empty; required fields make it fill each one.
    schema = DailyMailDraft.model_json_schema()

    assert set(schema["required"]) == set(DailyMailDraft.model_fields)
