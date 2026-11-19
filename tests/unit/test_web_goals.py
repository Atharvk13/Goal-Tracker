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


def test_pages_share_base_layout(web_environment, goal_form):
    client, service = web_environment

    client.post("/goals/review", data=goal_form)
    review = client.post("/goals/review", data=goal_form)
    parser = DraftParser()
    parser.feed(review.text)
    client.post("/goals/save", data={"draft_json": parser.draft_json})
    goal_id = service.list_goals()[0]["id"]

    pages = [
        "/",
        "/goals",
        "/goals/new",
        f"/goals/{goal_id}",
        f"/goals/{goal_id}/edit",
        f"/goals/{goal_id}/lifecycle",
        f"/goals/{goal_id}/plan",
    ]

    for url in pages:
        response = client.get(url)

        assert response.status_code == 200, url
        assert '<nav aria-label="Main">' in response.text, url
        assert response.text.count("<main") == 1, url


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

def test_goal_details_show_saved_plan(web_environment, goal_form):
    client, service = web_environment

    review = client.post("/goals/review", data=goal_form)
    parser = DraftParser()
    parser.feed(review.text)
    assert parser.draft_json is not None

    saved = client.post(
        "/goals/save",
        data={"draft_json": parser.draft_json},
        follow_redirects=False,
    )
    assert saved.status_code == 303

    goal = service.list_goals()[0]
    response = client.get(f"/goals/{goal['id']}")

    assert response.status_code == 200
    assert goal["title"] in response.text
    assert goal["period"]["start_date"] in response.text
    assert goal["period"]["end_date"] in response.text
    assert "Build the form" in response.text
    assert "Verify saving" in response.text
    assert "Included in current plan" in response.text

    listing = client.get("/goals")
    assert f'href="/goals/{goal["id"]}"' in listing.text


def test_missing_goal_returns_404(web_environment):
    from uuid import uuid4

    client, service = web_environment

    response = client.get(f"/goals/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "The requested goal does not exist."
    assert service.list_goals() == []


def test_invalid_goal_id_returns_422(web_environment):
    client, service = web_environment

    response = client.get("/goals/not-a-valid-id")

    assert response.status_code == 422
    assert service.list_goals() == []

def _create_editable_goal(service, goal_form):
    from goal_tracker.domain.models import GoalCreate

    return service.create_goal(
        GoalCreate(
            period={
                "kind": goal_form["kind"],
                "start_date": goal_form["start_date"],
                "timezone": "Asia/Kolkata",
            },
            title=goal_form["title"],
            description=goal_form["description"],
            expected_outcome=goal_form["expected_outcome"],
            milestones=[{"description": "Build the form"}],
        )
    )["goal"]


def _edit_form(goal, **changes):
    values = {
        "expected_revision": str(goal["revision"]),
        "title": goal["title"],
        "description": goal["description"],
        "expected_outcome": goal["expected_outcome"],
        "target_date": goal["target_date"] or "",
        "reason": "Clarify the intended outcome.",
        "effective_date": "",
    }
    values.update(changes)
    return values


def test_edit_review_and_confirmation_preserve_other_fields(
    web_environment,
    goal_form,
):
    client, service = web_environment
    goal = _create_editable_goal(service, goal_form)

    form = client.get(f"/goals/{goal['id']}/edit")
    assert form.status_code == 200
    assert goal["title"] in form.text

    review = client.post(
        f"/goals/{goal['id']}/edit/review",
        data=_edit_form(goal, description="A clearer description."),
    )

    assert review.status_code == 200
    assert service.get_goal(goal["id"])["revision"] == goal["revision"]

    parser = DraftParser()
    parser.feed(review.text)
    assert parser.draft_json is not None

    # Retrying the same confirmed draft must not add another revision.
    for _ in range(2):
        saved = client.post(
            f"/goals/{goal['id']}/edit/save",
            data={"draft_json": parser.draft_json},
            follow_redirects=False,
        )
        assert saved.status_code == 303

    updated = service.get_goal(goal["id"])

    assert updated["description"] == "A clearer description."
    assert updated["revision"] == goal["revision"] + 1
    assert updated["title"] == goal["title"]
    assert updated["expected_outcome"] == goal["expected_outcome"]
    assert updated["target_date"] == goal["target_date"]
    assert updated["milestones"] == goal["milestones"]


def test_edit_save_rejects_revision_changed_after_review(
    web_environment,
    goal_form,
):
    from goal_tracker.domain.goal_changes import GoalUpdate

    client, service = web_environment
    goal = _create_editable_goal(service, goal_form)

    review = client.post(
        f"/goals/{goal['id']}/edit/review",
        data=_edit_form(goal, title="My browser edit"),
    )
    assert review.status_code == 200

    parser = DraftParser()
    parser.feed(review.text)
    assert parser.draft_json is not None

    service.update_goal(
        GoalUpdate(
            goal_id=goal["id"],
            expected_revision=goal["revision"],
            reason="Simulate an edit from another window.",
            title="A newer saved title",
        )
    )

    response = client.post(
        f"/goals/{goal['id']}/edit/save",
        data={"draft_json": parser.draft_json},
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "changed before your edit was saved" in response.text
    assert service.get_goal(goal["id"])["title"] == "A newer saved title"


def test_edit_without_changes_is_rejected(web_environment, goal_form):
    client, service = web_environment
    goal = _create_editable_goal(service, goal_form)

    response = client.post(
        f"/goals/{goal['id']}/edit/review",
        data=_edit_form(goal),
    )

    assert response.status_code == 422
    assert "At least one goal change is required" in response.text
    assert service.get_goal(goal["id"])["revision"] == goal["revision"]

@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        ("cancel", "cancelled"),
        ("archive", "archived"),
    ],
)
def test_lifecycle_review_and_save_preserve_records(
    web_environment,
    goal_form,
    action,
    expected_status,
):
    client, service = web_environment
    goal = _create_editable_goal(service, goal_form)

    response = client.get(f"/goals/{goal['id']}/lifecycle")
    assert response.status_code == 200

    review = client.post(
        f"/goals/{goal['id']}/lifecycle/review",
        data={
            "expected_revision": str(goal["revision"]),
            "action": action,
            "reason": "My priorities have changed.",
            "effective_date": "",
        },
    )

    assert review.status_code == 200
    assert service.get_goal(goal["id"])["lifecycle_status"] == "active"

    parser = DraftParser()
    parser.feed(review.text)
    assert parser.draft_json is not None

    for _ in range(2):
        saved = client.post(
            f"/goals/{goal['id']}/lifecycle/save",
            data={"draft_json": parser.draft_json},
            follow_redirects=False,
        )
        assert saved.status_code == 303

    updated = service.get_goal(goal["id"])

    assert updated["lifecycle_status"] == expected_status
    assert updated["revision"] == goal["revision"] + 1
    assert updated["milestones"] == goal["milestones"]
    assert updated["title"] == goal["title"]
    assert len(service.list_goals()) == 1

def test_plan_finalization_requires_confirmation_and_handles_retries(
    web_environment,
    goal_form,
):
    client, service = web_environment
    goal = _create_editable_goal(service, goal_form)

    review = client.get(f"/goals/{goal['id']}/plan")

    assert review.status_code == 200
    assert service.get_goal(goal["id"])["period"]["planning_closed_at"] is None

    parser = DraftParser()
    parser.feed(review.text)
    assert parser.draft_json is not None

    for _ in range(2):
        saved = client.post(
            f"/goals/{goal['id']}/plan/save",
            data={"draft_json": parser.draft_json},
            follow_redirects=False,
        )
        assert saved.status_code == 303

    updated = service.get_goal(goal["id"])

    assert updated["period"]["planning_closed_at"] is not None
    assert updated["is_original_plan"] is True

    details = client.get(f"/goals/{goal['id']}")
    assert details.status_code == 200
    assert "Finalized" in details.text
    assert f'href="/goals/{goal["id"]}/plan"' not in details.text

    second_review = client.get(f"/goals/{goal['id']}/plan")
    assert second_review.status_code == 409
    assert "already finalized" in second_review.text