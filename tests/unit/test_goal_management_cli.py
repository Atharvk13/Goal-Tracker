import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from rich.console import Console

import goal_tracker.cli.goal_editing as editing_module
import goal_tracker.cli.goal_management as management_module
from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.migrate import migrate_database
from goal_tracker.domain.goal_changes import GoalLifecycleChange, GoalUpdate
from goal_tracker.domain.models import GoalCreate
from goal_tracker.goals.service import GoalService


@pytest.fixture
def management_environment(tmp_path, monkeypatch):
    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    service = GoalService(
        paths.database,
        today=lambda: date(2026, 11, 11),
    )
    goal_input = GoalCreate(
        period={
            "kind": "weekly",
            "start_date": "2026-11-09",
        },
        title="Learn LangChain",
        milestones=[
            {"description": "Complete the tutorial"},
            {"description": "Build a client"},
        ],
    )
    goal = service.create_goal(goal_input)["goal"]

    context = SimpleNamespace(
        settings=SimpleNamespace(timezone="Asia/Kolkata"),
        paths=paths,
    )

    output = io.StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        width=160,
    )

    monkeypatch.setattr(
        management_module,
        "GoalService",
        lambda database_path, *, timezone: service,
    )

    fixed_clock = SimpleNamespace(
        now=lambda zone: datetime(
            2026, 11, 11, 12, tzinfo=zone
        )
    )

    for module in (management_module, editing_module):
        monkeypatch.setattr(module, "datetime", fixed_clock)

    return SimpleNamespace(
        context=context,
        service=service,
        goal=goal,
        goal_input=goal_input,
        console=console,
        output=output,
    )


def run_management(environment, monkeypatch, answers):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO("\n".join(answers) + "\n"),
    )

    management_module.manage_goals(
        environment.context,
        environment.console,
    )

    return environment.output.getvalue()


def title_edit_answers(title, confirmation):
    return [
        "",     # Active filter
        "1",    # Select goal
        "1",    # Edit goal
        "1",    # Edit title
        title,
        "Make the outcome specific.",
        "",     # Normal effective date
        confirmation,
        "0",    # Back to goal list
        "0",    # Leave management
    ]


def test_browsing_displays_goal_without_changing_it(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    output = run_management(
        environment,
        monkeypatch,
        ["", "1", "0", "0"],
    )

    assert "Learn LangChain" in output
    assert "Complete the tutorial" in output
    assert environment.service.get_goal(
        environment.goal["id"]
    ) == environment.goal


def test_confirmed_title_edit_creates_revision(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    output = run_management(
        environment,
        monkeypatch,
        title_edit_answers("Build a local agent", "y"),
    )

    saved = environment.service.get_goal(environment.goal["id"])

    assert saved["title"] == "Build a local agent"
    assert saved["revision"] == 2
    assert "Goal updated." in output
    assert output.index("Review Goal Changes") < output.index(
        "Save these goal changes?"
    )


def test_declined_edit_preserves_goal_and_audit_history(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    run_management(
        environment,
        monkeypatch,
        title_edit_answers("Do not save this title", "n"),
    )

    assert environment.service.get_goal(
        environment.goal["id"]
    ) == environment.goal

    history = environment.service.get_goal_history(environment.goal["id"])
    assert len(history["audit_events"]) == 1


@pytest.mark.parametrize(
    ("action_number", "expected_status"),
    [
        ("3", "cancelled"),
        ("4", "archived"),
    ],
)
def test_confirmed_lifecycle_change_preserves_records(
    management_environment,
    monkeypatch,
    action_number,
    expected_status,
):
    environment = management_environment

    run_management(
        environment,
        monkeypatch,
        [
            "",
            "1",
            action_number,
            "Change priorities.",
            "",
            "y",
            "0",
            "0",
        ],
    )

    saved = environment.service.get_goal(environment.goal["id"])

    assert saved["lifecycle_status"] == expected_status
    assert saved["revision"] == 2
    assert {
        milestone["id"] for milestone in saved["milestones"]
    } == {
        milestone["id"] for milestone in environment.goal["milestones"]
    }


def test_declined_cancellation_leaves_goal_active(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    run_management(
        environment,
        monkeypatch,
        ["", "1", "3", "Testing cancellation.", "", "n", "0", "0"],
    )

    saved = environment.service.get_goal(environment.goal["id"])

    assert saved["lifecycle_status"] == "active"
    assert saved["revision"] == 1


def test_add_milestone_through_editor(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    run_management(
        environment,
        monkeypatch,
        [
            "",
            "1",
            "1",
            "5",
            "Write integration tests",
            "",
            "Expand the plan.",
            "",
            "y",
            "0",
            "0",
        ],
    )

    saved = environment.service.get_goal(environment.goal["id"])
    added = next(
        milestone for milestone in saved["milestones"]
        if milestone["description"] == "Write integration tests"
    )

    assert saved["revision"] == 2
    assert len(saved["milestones"]) == 3
    assert added["is_baseline"] is False


@pytest.mark.parametrize("restore", [False, True])
def test_remove_or_restore_milestone(
    management_environment,
    monkeypatch,
    restore,
):
    environment = management_environment
    milestone_id = environment.goal["milestones"][0]["id"]

    if restore:
        environment.service.update_goal(
            GoalUpdate(
                goal_id=environment.goal["id"],
                expected_revision=1,
                reason="Prepare a removed milestone.",
                milestone_changes=[
                    {
                        "milestone_id": milestone_id,
                        "is_included": False,
                    }
                ],
            )
        )

    run_management(
        environment,
        monkeypatch,
        ["", "1", "1", "7", "1", "Adjust milestone scope.", "", "y", "0", "0"],
    )

    saved = environment.service.get_goal(environment.goal["id"])
    milestone = next(
        item for item in saved["milestones"]
        if item["id"] == milestone_id
    )

    assert milestone["is_included"] is restore
    assert milestone["is_baseline"] is True
    assert saved["revision"] == (3 if restore else 2)


def test_deadline_editor_can_adjust_affected_milestones(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    run_management(
        environment,
        monkeypatch,
        [
            "",
            "1",
            "1",
            "4",
            "2026-11-12",
            "y",
            "Adjust the schedule.",
            "",
            "y",
            "0",
            "0",
        ],
    )

    saved = environment.service.get_goal(environment.goal["id"])

    assert saved["target_date"] == "2026-11-12"
    assert all(
        milestone["due_date"] == "2026-11-12"
        for milestone in saved["milestones"]
    )


def test_history_can_be_viewed_without_writes(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    output = run_management(
        environment,
        monkeypatch,
        ["", "1", "2", "0", "0", "0"],
    )

    assert "Goal Revision History" in output
    assert "Milestone Revision History" in output
    assert "Audit Events" in output
    assert environment.service.get_goal(
        environment.goal["id"]
    ) == environment.goal


def test_filter_can_switch_to_cancelled_goals(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    environment.service.change_lifecycle(
        GoalLifecycleChange(
            goal_id=environment.goal["id"],
            expected_revision=1,
            action="cancel",
            reason="Prepare a cancelled goal.",
        )
    )

    output = run_management(
        environment,
        monkeypatch,
        ["", "f", "cancelled", "1", "0", "0"],
    )

    assert "No goals match this selection." in output
    assert "Learn LangChain" in output
    assert environment.service.get_goal(
        environment.goal["id"]
    )["lifecycle_status"] == "cancelled"


def test_initial_plan_can_be_finalized_from_management(
    management_environment,
    monkeypatch,
):
    environment = management_environment

    output = run_management(
        environment,
        monkeypatch,
        ["", "1", "5", "y", "0", "0"],
    )

    saved = environment.service.get_goal(environment.goal["id"])

    assert saved["period"]["planning_closed_at"] is not None
    assert "Initial plan finalized." in output


def test_goal_list_supports_next_and_previous_pages(
    management_environment,
    monkeypatch,
):
    environment = management_environment
    monkeypatch.setattr(management_module, "PAGE_SIZE", 2)

    for title in ("Second goal", "Third goal"):
        environment.service.create_goal(
            GoalCreate(
                period=environment.goal_input.period,
                title=title,
                milestones=[{"description": "Finish the task"}],
            )
        )

    output = run_management(
        environment,
        monkeypatch,
        ["all", "n", "p", "0"],
    )

    assert output.count("Page 1") == 2
    assert output.count("Page 2") == 1
    assert len(environment.service.list_goals()) == 3


def test_history_supports_audit_pagination(
    management_environment,
    monkeypatch,
):
    environment = management_environment
    monkeypatch.setattr(management_module, "PAGE_SIZE", 2)

    for revision, title in (
        (1, "First edited title"),
        (2, "Second edited title"),
    ):
        environment.service.update_goal(
            GoalUpdate(
                goal_id=environment.goal["id"],
                expected_revision=revision,
                title=title,
                reason="Prepare history entries.",
            )
        )

    output = run_management(
        environment,
        monkeypatch,
        ["", "1", "2", "n", "p", "0", "0", "0"],
    )

    assert "offset 2" in output
    assert environment.service.get_goal(
        environment.goal["id"]
    )["revision"] == 3