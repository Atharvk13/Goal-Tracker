"""AI goal suggestions and the Monday-to-Friday week plan."""

import json
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from conftest import input_values
from goal_tracker.assist.planning import (
    GOAL_REVIEW,
    WEEK_PLAN,
    goal_review_input,
    week_to_plan,
)
from goal_tracker.assist.store import SuggestionStore
from goal_tracker.domain.models import GoalCreate
from goal_tracker.llm import gateway as gateway_module
from goal_tracker.llm.assist_schemas import DayPlan, GoalReview, WeekPlan

THURSDAY = date(2026, 11, 19)
MONDAY = date(2026, 11, 16)


def _review(**overrides) -> GoalReview:
    values = {
        "milestone_ideas": ["Deploy the app to a test server"],
        "tips": ["Block one hour each morning"],
        "risks": ["Scope creep; keep to two pages"],
    }
    return GoalReview(**{**values, **overrides})


def _create_goal(web, *, start: date | None = None, milestones=("Build the form", "Write tests")):
    if start is None:
        start, _ = week_to_plan(web.today)
    result = web.goals.create_goal(
        GoalCreate.model_validate(
            {
                "period": {
                    "kind": "weekly",
                    "start_date": start.isoformat(),
                    "timezone": web.context.settings.timezone,
                },
                "title": "Ship the web app",
                "expected_outcome": "A working web app",
                "milestones": [{"description": text} for text in milestones],
            }
        )
    )
    return result["goal"]


# ------------------------------------------------------------- pure logic


@pytest.mark.parametrize(
    ("today", "requested", "monday", "days"),
    [
        (THURSDAY, None, MONDAY, ["Thursday", "Friday"]),
        (MONDAY, None, MONDAY, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
        # Weekend: plan the coming week in full.
        (date(2026, 11, 21), None, date(2026, 11, 23), ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
        (date(2026, 11, 22), None, date(2026, 11, 23), ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
        # A future week is planned in full; a past week is not offered.
        (THURSDAY, date(2026, 11, 25), date(2026, 11, 23), ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
        (THURSDAY, date(2026, 11, 2), MONDAY, ["Thursday", "Friday"]),
    ],
)
def test_week_to_plan_covers_only_working_days(today, requested, monday, days):
    assert week_to_plan(today, requested) == (monday, days)


def test_goal_review_input_lists_only_included_milestones():
    goal = {
        "title": "T",
        "description": "",
        "expected_outcome": "",
        "target_date": None,
        "period": {"kind": "weekly", "start_date": "2026-11-16", "end_date": "2026-11-22"},
        "milestones": [
            {"description": "kept", "is_included": True},
            {"description": "removed", "is_included": False},
        ],
    }

    data = goal_review_input(goal)

    assert data["milestones"] == ["kept"]
    assert data["deadline"] == "2026-11-22"


def test_schema_fields_are_required_and_cleaned():
    # Optional fields made the small model return empty output.
    assert set(GoalReview.model_json_schema()["required"]) == set(GoalReview.model_fields)
    assert set(WeekPlan.model_json_schema()["required"]) == set(WeekPlan.model_fields)
    assert set(DayPlan.model_json_schema()["required"]) == set(DayPlan.model_fields)

    review = GoalReview(milestone_ideas=[" A ", "a", ""], tips=[], risks=[])
    assert review.milestone_ideas == ["A"]

    with pytest.raises(ValueError):
        DayPlan(day="Saturday", focus="", tasks=[])


def test_store_replaces_suggestions_for_the_same_subject(web):
    store = SuggestionStore(web.paths.database)

    store.save(GOAL_REVIEW, "g1", evidence_hash="h1", content={"a": 1}, model="m")
    saved = store.save(GOAL_REVIEW, "g1", evidence_hash="h2", content={"a": 2}, model="m")

    assert saved["content"] == {"a": 2}
    assert saved["evidence_hash"] == "h2"
    assert store.get(WEEK_PLAN, "g1") is None


# ---------------------------------------------------------------- gateway


@pytest.fixture
def simulated_gateway(monkeypatch):
    model = Mock()
    runner = Mock()
    model.with_structured_output.return_value = runner
    monkeypatch.setattr(gateway_module, "create_model", lambda settings: model)
    gateway = gateway_module.ModelGateway(
        SimpleNamespace(llm_structured_method="json_schema")
    )
    return gateway, runner


def test_gateway_review_drops_existing_milestones(simulated_gateway):
    gateway, runner = simulated_gateway
    runner.invoke.return_value = {
        "parsing_error": None,
        "parsed": _review(milestone_ideas=["Write tests", "Add login"]),
    }

    review = gateway.review_goal({"title": "Ship", "milestones": ["write TESTS"]})

    assert review.milestone_ideas == ["Add login"]
    system, human = runner.invoke.call_args.args[0]
    assert "Goal Review" in system[1]
    assert json.loads(human[1])["title"] == "Ship"


def test_gateway_week_plan_keeps_requested_days_in_order(simulated_gateway):
    gateway, runner = simulated_gateway
    runner.invoke.return_value = {
        "parsing_error": None,
        "parsed": WeekPlan(
            days=[
                DayPlan(day="Friday", focus="Wrap up", tasks=["Write tests"]),
                DayPlan(day="Monday", focus="Too early", tasks=["x"]),
                DayPlan(day="Friday", focus="Duplicate", tasks=[]),
            ],
            notes=[],
        ),
    }

    plan = gateway.plan_week({"goals": []}, ["Thursday", "Friday"])

    assert [day.day for day in plan.days] == ["Thursday", "Friday"]
    assert plan.days[0].tasks == []
    assert plan.days[1].focus == "Wrap up"
    assert json.loads(runner.invoke.call_args.args[0][1][1])["days_to_plan"] == [
        "Thursday",
        "Friday",
    ]

    with pytest.raises(ValueError, match="Monday to Friday"):
        gateway.plan_week({}, ["Saturday"])


def test_gateway_rejects_invalid_output(simulated_gateway):
    gateway, runner = simulated_gateway
    runner.invoke.return_value = {"parsing_error": ValueError(), "parsed": None}

    with pytest.raises(ValueError, match="invalid GoalReview"):
        gateway.review_goal({"title": "Ship", "milestones": []})


# ------------------------------------------------------- goal suggestions


def test_saving_a_goal_opens_it_and_asks_for_suggestions(web):
    start, _ = week_to_plan(web.today)
    review = web.client.post(
        "/goals/review",
        data={
            "kind": "weekly",
            "start_date": start.isoformat(),
            "title": "Learn FastAPI",
            "milestones": "Read the tutorial",
        },
    )
    [draft_json] = input_values(review.text, "draft_json")

    response = web.client.post(
        "/goals/save", data={"draft_json": draft_json}, follow_redirects=False
    )

    goal_id = web.goals.list_goals()[0]["id"]
    assert response.status_code == 303
    assert response.headers["location"] == f"/goals/{goal_id}?suggest=1"

    page = web.client.get(response.headers["location"])
    assert "AI suggestions" in page.text
    assert "Get AI suggestions" in page.text
    assert "run(document.querySelector" in page.text  # asks once, in the background


def test_suggestions_are_generated_stored_and_shown(web):
    goal = _create_goal(web)
    web.gateway.goal_review = _review()

    response = web.client.post(f"/goals/{goal['id']}/suggestions", follow_redirects=False)

    assert response.status_code == 303
    assert sorted(web.gateway.review_input["milestones"]) == ["Build the form", "Write tests"]

    page = web.client.get(f"/goals/{goal['id']}").text
    assert "Deploy the app to a test server" in page
    assert "Add this milestone" in page
    assert "Block one hour each morning" in page
    assert "Regenerate suggestions" in page
    assert "run(document.querySelector" not in page  # stored: not asked again


def test_fragment_request_returns_only_the_section(web):
    goal = _create_goal(web)
    web.gateway.goal_review = _review()

    response = web.client.post(
        f"/goals/{goal['id']}/suggestions", headers={"X-Fragment": "1"}
    )

    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section class="card ai-card" id="ai-suggestions"')
    assert "<nav" not in response.text


def test_model_failure_keeps_the_goal_page_usable(web):
    goal = _create_goal(web)
    web.gateway.error = RuntimeError("down")

    response = web.client.post(f"/goals/{goal['id']}/suggestions")

    assert response.status_code == 503
    assert "local model is unavailable" in response.text
    assert SuggestionStore(web.paths.database).get(GOAL_REVIEW, goal["id"]) is None


def test_suggestions_become_stale_after_the_goal_changes(web):
    goal = _create_goal(web)
    web.gateway.goal_review = _review()
    web.client.post(f"/goals/{goal['id']}/suggestions")

    review = web.client.post(
        f"/goals/{goal['id']}/suggestions/milestone",
        data={"description": "Deploy the app to a test server"},
    )
    [draft_json] = input_values(review.text, "draft_json")
    web.client.post(f"/goals/{goal['id']}/edit/save", data={"draft_json": draft_json})

    page = web.client.get(f"/goals/{goal['id']}").text
    assert "The goal changed after these suggestions were made" in page


def test_adding_a_suggested_milestone_goes_through_review(web):
    goal = _create_goal(web)

    review = web.client.post(
        f"/goals/{goal['id']}/suggestions/milestone",
        data={"description": "Deploy the app to a test server"},
    )

    assert review.status_code == 200
    assert "New milestone" in review.text
    assert "Confirm and save changes" in review.text
    assert len(web.goals.get_goal(goal["id"])["milestones"]) == 2  # not saved yet

    [draft_json] = input_values(review.text, "draft_json")
    saved = web.client.post(
        f"/goals/{goal['id']}/edit/save",
        data={"draft_json": draft_json},
        follow_redirects=False,
    )

    assert saved.status_code == 303
    descriptions = [m["description"] for m in web.goals.get_goal(goal["id"])["milestones"]]
    assert "Deploy the app to a test server" in descriptions


def test_independent_goal_has_no_suggestions(web):
    from goal_tracker.domain.enums import PeriodKind
    from goal_tracker.domain.models import PeriodSpec

    start = web.today - timedelta(days=web.today.weekday())
    independent = web.goals.ensure_independent_goal(
        PeriodSpec(kind=PeriodKind.WEEKLY, start_date=start, timezone=web.context.settings.timezone)
    )

    page = web.client.get(f"/goals/{independent['id']}")
    assert "AI suggestions" not in page.text
    assert web.client.post(f"/goals/{independent['id']}/suggestions").status_code == 400
    assert "review_goal" not in web.gateway.calls


# -------------------------------------------------------------- week plan


def test_week_plan_needs_something_to_plan(web):
    page = web.client.get("/week-plan")

    assert page.status_code == 200
    assert "Monday to Friday" in page.text
    assert "No active goal has open milestones" in page.text

    response = web.client.post("/week-plan")
    assert response.status_code == 422
    assert "nothing to plan" in response.text
    assert "plan_week" not in web.gateway.calls


def test_week_plan_uses_open_milestones_and_is_stored(web):
    _create_goal(web)
    monday, days = week_to_plan(web.today)
    web.gateway.week_plan = WeekPlan(
        days=[DayPlan(day=day, focus=f"Focus {day}", tasks=["Build the form"]) for day in days],
        notes=["Keep Friday for testing"],
    )

    response = web.client.post("/week-plan", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/week-plan?week={monday.isoformat()}"
    assert web.gateway.week_days == days
    [goal] = web.gateway.week_input["goals"]
    assert sorted(m["milestone"] for m in goal["open_milestones"]) == ["Build the form", "Write tests"]

    page = web.client.get(response.headers["location"]).text
    for day in days:
        assert f"Focus {day}" in page
    assert "Keep Friday for testing" in page
    assert "Regenerate plan" in page
    assert "Saturday" not in page and "Sunday" not in page


def test_week_plan_model_failure_is_reported(web):
    _create_goal(web)
    web.gateway.error = RuntimeError("down")

    response = web.client.post("/week-plan")

    assert response.status_code == 503
    assert "local model is unavailable" in response.text


def test_week_plan_page_in_navigation_and_dashboard(web):
    assert 'href="/week-plan"' in web.client.get("/").text
