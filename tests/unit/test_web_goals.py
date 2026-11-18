from datetime import datetime, timedelta
from html.parser import HTMLParser
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import goal_tracker.web.app as web_module
from goal_tracker.bootstrap import AppContext
from goal_tracker.config.paths import get_app_paths
from goal_tracker.config.settings import Settings
from goal_tracker.db.migrate import migrate_database
from goal_tracker.goals.service import GoalService


class DraftParser(HTMLParser):
    """Read the hidden draft from the review page."""

    def __init__(self):
        super().__init__()
        self.draft_json = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)

        if tag == "input" and attributes.get("name") == "draft_json":
            self.draft_json = attributes.get("value")


@pytest.fixture
def web_environment(tmp_path, monkeypatch):
    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    context = AppContext(
        settings=Settings(
            llm_model="test-model",
            timezone="Asia/Kolkata",
        ),
        paths=paths,
        logger=Mock(),
    )

    monkeypatch.setattr(
        web_module,
        "bootstrap",
        Mock(return_value=context),
    )

    service = GoalService(
        paths.database,
        timezone=context.settings.timezone,
    )

    with TestClient(web_module.create_app()) as client:
        yield client, service


@pytest.fixture
def goal_form():
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    monday = today - timedelta(days=today.weekday())

    return {
        "kind": "weekly",
        "start_date": monday.isoformat(),
        "title": "Learn web development",
        "description": "Build a browser interface.",
        "expected_outcome": "Create and review goals in the browser.",
        "target_date": "",
        "milestones": "Build the form\nVerify saving",
    }


def test_dashboard_and_goal_form_load(web_environment):
    client, service = web_environment

    dashboard = client.get("/")
    form = client.get("/goals/new")

    assert dashboard.status_code == 200
    assert "test-model" in dashboard.text
    assert form.status_code == 200
    assert 'action="/goals/review"' in form.text
    assert service.list_goals() == []


def test_review_does_not_save_goal(web_environment, goal_form):
    client, service = web_environment

    response = client.post("/goals/review", data=goal_form)

    assert response.status_code == 200
    assert "Learn web development" in response.text
    assert "Confirm and save" in response.text
    assert service.list_goals() == []


def test_confirmation_saves_once_on_repeated_submission(
    web_environment,
    goal_form,
):
    client, service = web_environment

    review = client.post("/goals/review", data=goal_form)
    assert review.status_code == 200

    parser = DraftParser()
    parser.feed(review.text)
    assert parser.draft_json is not None

    for _ in range(2):
        response = client.post(
            "/goals/save",
            data={"draft_json": parser.draft_json},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/goals"

    goals = service.list_goals()

    assert len(goals) == 1
    assert goals[0]["title"] == goal_form["title"]
    assert len(goals[0]["milestones"]) == 2

    listing = client.get("/goals")
    assert listing.status_code == 200
    assert goal_form["title"] in listing.text


def test_duplicate_milestones_are_rejected(web_environment, goal_form):
    client, service = web_environment
    goal_form["milestones"] = "Build the form\nBUILD THE FORM"

    response = client.post("/goals/review", data=goal_form)

    assert response.status_code == 422
    assert "Milestone descriptions must be unique" in response.text
    assert goal_form["title"] in response.text
    assert service.list_goals() == []


def test_invalid_draft_is_not_saved(web_environment):
    client, service = web_environment

    response = client.post(
        "/goals/save",
        data={"draft_json": "invalid json"},
    )

    assert response.status_code == 422
    assert "The goal draft is invalid" in response.text
    assert service.list_goals() == []