"""Chat assistant grounded in saved records."""

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from goal_tracker.assist.chat import (
    NO_RECORDS_ANSWER,
    Fact,
    collect_facts,
    select_facts,
)
from goal_tracker.assist.planning import week_to_plan
from goal_tracker.domain.models import GoalCreate
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.llm import gateway as gateway_module
from goal_tracker.llm.assist_schemas import ChatAnswer
from goal_tracker.mcp import services as service_factories

TODAY = date(2026, 11, 20)


def _seed(web):
    monday = web.today - timedelta(days=web.today.weekday())
    goal = web.goals.create_goal(
        GoalCreate.model_validate(
            {
                "period": {
                    "kind": "weekly",
                    "start_date": monday.isoformat(),
                    "timezone": web.context.settings.timezone,
                },
                "title": "Learn LangChain",
                "milestones": [{"description": "Finish the tool-calling exercise"}],
            }
        )
    )["goal"]
    web.progress.record_progress(
        DailyProgressInput.model_validate(
            {
                "activity_date": web.today.isoformat(),
                "raw_text": "Read the LangChain docs. Learned about retrievers.",
                "activities": [
                    {
                        "description": "Read the LangChain docs",
                        "state": "completed",
                        "minutes": 45,
                        "goal_ids": [goal["id"]],
                    },
                    {
                        "kind": "learning",
                        "description": "Learned about retrievers",
                        "state": "not_applicable",
                    },
                    {
                        "description": "Set up the vector store",
                        "state": "pending",
                    },
                ],
            }
        )
    )
    return goal


def _facts(web):
    return collect_facts(
        service_factories.get_goal_service(web.context),
        service_factories.get_analysis_service(web.context),
        service_factories.get_progress_service(web.context),
        web.today,
    )


# ------------------------------------------------------------ facts


def test_facts_cover_goals_journal_and_pending_work(web):
    _seed(web)

    texts = {fact.id: fact.text for fact in _facts(web)}

    goal = next(text for key, text in texts.items() if key.startswith("G"))
    assert 'goal "Learn LangChain"' in goal
    assert "Finish the tool-calling exercise (not done" in goal
    assert any("Work: Read the LangChain docs (completed; 45 min; goal: Learn LangChain)" in t for t in texts.values())
    assert any("Learning: Learned about retrievers." in t for t in texts.values())
    assert any(key.startswith("P") and "Set up the vector store" in t for key, t in texts.items())


def test_selection_prefers_matching_words_and_dates():
    facts = [
        Fact("A1", "2026-11-20: Work: Wrote unit tests.", TODAY, "journal"),
        Fact("A2", "2026-11-19: Learning: Learned about retrievers.", TODAY - timedelta(days=1), "journal"),
        Fact("A3", "2026-10-01: Work: Fixed the PDF export.", date(2026, 10, 1), "journal"),
    ]

    assert select_facts("What did I learn about retrievers?", facts, TODAY)[0].id == "A2"
    assert select_facts("What did I do yesterday?", facts, TODAY)[0].id == "A2"
    assert select_facts("pdf export", facts, TODAY)[0].id == "A3"


def test_selection_respects_the_context_budget():
    facts = [Fact(f"A{i}", "x" * 100, TODAY, "journal") for i in range(50)]

    chosen = select_facts("anything", facts, TODAY, budget=1000)

    assert 0 < len(chosen) < 10
    assert sum(len(fact.text) for fact in chosen) <= 1000


# ---------------------------------------------------------- gateway


@pytest.fixture
def simulated_gateway(monkeypatch):
    model = Mock()
    runner = Mock()
    model.with_structured_output.return_value = runner
    monkeypatch.setattr(gateway_module, "create_model", lambda settings: model)
    return gateway_module.ModelGateway(SimpleNamespace(llm_structured_method="json_schema")), runner


def _model_says(runner, **values):
    runner.invoke.return_value = {"parsing_error": None, "parsed": ChatAnswer(**values)}


def test_gateway_sends_only_records_and_keeps_known_sources(simulated_gateway):
    gateway, runner = simulated_gateway
    _model_says(runner, answered=True, answer="You read the docs.", sources=["A1", "Z9", "A1"])

    result = gateway.answer_from_records("What did I do?", {"A1": "Read docs."}, today="2026-11-20")

    assert result.answered and result.sources == ["A1"]
    system, human = runner.invoke.call_args.args[0]
    assert "Records Chat" in system[1]
    assert json.loads(human[1])["records"] == {"A1": "Read docs."}


@pytest.mark.parametrize(
    "values",
    [
        {"answered": False, "answer": "No idea.", "sources": []},
        # Claims an answer but cites nothing, or only unknown records.
        {"answered": True, "answer": "Paris is the capital of France.", "sources": []},
        {"answered": True, "answer": "Something.", "sources": ["G99"]},
    ],
)
def test_gateway_treats_uncited_answers_as_not_answered(simulated_gateway, values):
    gateway, runner = simulated_gateway
    _model_says(runner, **values)

    result = gateway.answer_from_records("Question?", {"A1": "Read docs."}, today="2026-11-20")

    assert result.answered is False
    assert result.sources == []


# ------------------------------------------------------------ route


def test_no_records_means_no_model_call(web):
    response = web.client.post("/chat/ask", json={"question": "What are my goals?"})

    assert response.json() == {"answered": False, "answer": NO_RECORDS_ANSWER, "sources": [], "proposal": None}
    assert "answer_from_records" not in web.gateway.calls


def test_answer_includes_cited_records(web):
    _seed(web)
    web.gateway.chat_answer = ChatAnswer(answered=True, answer="You learned about retrievers.", sources=["A1"])

    response = web.client.post(
        "/chat/ask",
        json={
            "question": "What did I learn?",
            "history": [{"question": "hi", "answer": "Hello"}],
        },
    )

    data = response.json()
    assert data["answered"] is True
    assert data["answer"] == "You learned about retrievers."
    assert data["sources"][0]["id"] == "A1"
    assert data["sources"][0]["text"] == web.gateway.chat_input["records"]["A1"]
    assert web.gateway.chat_input["today"] == web.today.isoformat()
    assert web.gateway.chat_input["history"] == [{"question": "hi", "answer": "Hello"}]


def test_unanswerable_question_gets_the_fixed_reply(web):
    _seed(web)
    web.gateway.chat_answer = ChatAnswer(answered=False, answer="The capital is Paris.", sources=[])

    data = web.client.post("/chat/ask", json={"question": "Capital of France?"}).json()

    assert data == {"answered": False, "answer": NO_RECORDS_ANSWER, "sources": [], "proposal": None}
    assert "Paris" not in json.dumps(data)


def test_model_failure_is_reported(web):
    _seed(web)
    web.gateway.error = RuntimeError("down")

    response = web.client.post("/chat/ask", json={"question": "What is pending?"})

    assert response.status_code == 503
    assert "local model is unavailable" in response.json()["answer"]


def test_blank_question_is_rejected(web):
    assert web.client.post("/chat/ask", json={"question": "   "}).status_code == 422
    assert web.client.post("/chat/ask", json={"question": ""}).status_code == 422


def test_chat_widget_is_on_every_page(web):
    for url in ("/", "/goals", "/mail", "/week-plan"):
        page = web.client.get(url).text
        assert "data-chat-toggle" in page, url
        assert "Jarvis" in page, url
        assert 'class="jarvis' in page, url  # animated avatar
