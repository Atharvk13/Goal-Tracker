from datetime import timedelta

import pytest

from conftest import TIMEZONE, input_values
from goal_tracker.domain.models import GoalCreate, PeriodSpec
from goal_tracker.domain.progress import DailyProgressInput
from goal_tracker.goals.service import GoalService
from goal_tracker.llm.recommendations import MilestoneSuggestions, ReportRecommendations
from goal_tracker.progress.service import ProgressService


@pytest.fixture
def last_week(web):
    """Seed a goal and a completed milestone in the previous week."""
    monday = web.today - timedelta(days=web.today.weekday())
    period = PeriodSpec(kind="weekly", start_date=monday - timedelta(days=7), timezone=TIMEZONE)
    seed_date = period.start_date + timedelta(days=2)

    goal = GoalService(
        web.paths.database, timezone=TIMEZONE, today=lambda: seed_date
    ).create_goal(
        GoalCreate(
            period=period,
            title="Learn local agents",
            milestones=[{"description": "Complete the tutorial"}],
        )
    )["goal"]

    ProgressService(
        web.paths.database, timezone=TIMEZONE, today=lambda: seed_date
    ).record_progress(
        DailyProgressInput(
            activity_date=seed_date,
            raw_text="Completed the tutorial.",
            activities=[
                {
                    "description": "Completed the tutorial",
                    "state": "completed",
                    "minutes": 30,
                    "goal_ids": [goal["id"]],
                    "milestone_changes": [
                        {"milestone_id": goal["milestones"][0]["id"], "state": "done"}
                    ],
                }
            ],
        )
    )

    return goal["period"]


def _generate(web, period_id, **data):
    page = web.client.get(f"/reports/periods/{period_id}")
    request_id = input_values(page.text, "request_id")[0]
    return web.client.post(
        f"/reports/periods/{period_id}/generate",
        data={"request_id": request_id, **data},
        follow_redirects=False,
    )


def test_dashboard_reminds_about_unreported_periods(web, last_week):
    page = web.client.get("/")

    assert page.status_code == 200
    assert "Completed periods without saved reports" in page.text
    assert f"/reports/periods/{last_week['id']}" in page.text


def test_factual_report_is_saved_viewed_and_exported(web, last_week):
    listing = web.client.get("/reports")
    assert "No report yet" in listing.text

    saved = _generate(web, last_week["id"])
    assert saved.status_code == 303

    report = web.client.get(saved.headers["location"])
    assert report.status_code == 200
    assert "Report saved." in report.text
    assert "Learn local agents" in report.text
    assert "Complete the tutorial" in report.text
    assert "AI recommendations are not included" in report.text

    pdf = web.client.get(saved.headers["location"].split("?")[0] + "/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    assert "No report yet" not in web.client.get("/reports").text
    assert "Completed periods without saved reports" not in web.client.get("/").text


def test_ai_advice_is_previewed_before_saving(web, last_week):
    web.gateway.recommendations = ReportRecommendations(
        areas_for_improvement=[],
        next_period_suggestions=[
            {
                "text": "Build on the finished tutorial.",
                "evidence_ids": [f"period:{last_week['id']}"],
            }
        ],
        recommended_goals=[],
    )

    preview = _generate(web, last_week["id"], include_ai="on")
    assert preview.status_code == 200
    assert "Preview AI suggestions" in preview.text
    assert "Build on the finished tutorial." in preview.text
    assert web.client.get("/reports").text.count("No report yet") == 1

    form = {
        name: input_values(preview.text, name)[0]
        for name in ("request_id", "advisory_json", "evidence_hash", "provenance_json")
    }
    saved = web.client.post(
        f"/reports/periods/{last_week['id']}/save",
        data=form,
        follow_redirects=False,
    )
    assert saved.status_code == 303

    report = web.client.get(saved.headers["location"])
    assert "Build on the finished tutorial." in report.text
    assert "Model: test-model" in report.text


def test_ai_failure_still_allows_factual_report(web, last_week):
    web.gateway.error = TimeoutError()

    response = _generate(web, last_week["id"], include_ai="on")

    assert response.status_code == 503
    assert "AI suggestions are unavailable" in response.text
    assert input_values(response.text, "request_id")


def test_unfinished_period_cannot_be_reported(web):
    monday = web.today - timedelta(days=web.today.weekday())
    goal = web.goals.create_goal(
        GoalCreate(
            period=PeriodSpec(kind="weekly", start_date=monday, timezone=TIMEZONE),
            title="Current goal",
            milestones=[{"description": "Do it"}],
        )
    )["goal"]

    response = web.client.get(f"/reports/periods/{goal['period']['id']}")

    assert response.status_code == 404


def test_summary_shows_current_week(web):
    monday = web.today - timedelta(days=web.today.weekday())
    web.goals.create_goal(
        GoalCreate(
            period=PeriodSpec(kind="weekly", start_date=monday, timezone=TIMEZONE),
            title="Current goal",
            milestones=[{"description": "Do it"}],
        )
    )

    page = web.client.get("/summary")

    assert page.status_code == 200
    assert "Current goal" in page.text
    assert monday.isoformat() in page.text


def test_summary_rejects_invalid_period_start(web):
    monday = web.today - timedelta(days=web.today.weekday())

    page = web.client.get(
        f"/summary?kind=weekly&start={(monday + timedelta(days=1)).isoformat()}"
    )

    assert page.status_code == 422


def test_goal_form_adds_ai_milestone_suggestions(web):
    monday = web.today - timedelta(days=web.today.weekday())
    web.gateway.suggestions = MilestoneSuggestions(
        milestones=["Existing step", "Write the tests"]
    )

    response = web.client.post(
        "/goals/suggest",
        data={
            "kind": "weekly",
            "start_date": monday.isoformat(),
            "title": "Ship the web UI",
            "milestones": "existing step",
        },
    )

    assert response.status_code == 200
    assert "existing step\nWrite the tests</textarea>" in response.text
    assert "Added 1 AI suggestion(s)" in response.text
    assert web.goals.list_goals() == []


def test_goal_suggestions_handle_model_failure(web):
    monday = web.today - timedelta(days=web.today.weekday())
    web.gateway.error = ConnectionError()

    response = web.client.post(
        "/goals/suggest",
        data={"kind": "weekly", "start_date": monday.isoformat(), "title": "Goal"},
    )

    assert response.status_code == 503
    assert "AI planning is unavailable (ConnectionError)" in response.text


def test_every_page_uses_the_shared_layout(web, last_week):
    for url in ("/", "/progress", "/progress/new", "/progress/pending",
                "/summary", "/reports", f"/reports/periods/{last_week['id']}"):
        response = web.client.get(url)

        assert response.status_code == 200, url
        assert '<nav aria-label="Main">' in response.text, url
