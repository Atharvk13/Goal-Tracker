import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from rich.console import Console

import goal_tracker.cli.goal_creation as creation_module
from goal_tracker.config.paths import get_app_paths
from goal_tracker.db.connection import open_database
from goal_tracker.db.migrate import migrate_database
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.errors import StorageError
from goal_tracker.goals.service import GoalService


@pytest.fixture
def creation_environment(tmp_path, monkeypatch):
    paths = get_app_paths(tmp_path / "application-data")
    migrate_database(paths.database, paths.backups)

    service = GoalService(
        paths.database,
        today=lambda: date(2026, 11, 11),
    )

    context = SimpleNamespace(
        settings=SimpleNamespace(timezone="Asia/Kolkata"),
        paths=paths,
    )

    output = io.StringIO()
    console = Console(
        file=output,
        force_terminal=False,
        width=120,
    )

    monkeypatch.setattr(
        creation_module,
        "GoalService",
        lambda database_path, *, timezone: service,
    )
    monkeypatch.setattr(
        creation_module,
        "datetime",
        SimpleNamespace(
            now=lambda zone: datetime(
                2026, 11, 11, 12, tzinfo=zone
            )
        ),
    )

    return SimpleNamespace(
        context=context,
        service=service,
        console=console,
        output=output,
    )


def goal_answers(title="Learn LangChain", save="y"):
    """Answers from the goal title through the save confirmation."""
    return [
        title,
        "",  # Description
        "",  # Expected outcome
        "",  # Default goal deadline
        "Complete tutorial",
        "",  # Default milestone deadline
        "",  # Finish adding milestones
        save,
    ]


def run_creation(
    environment,
    monkeypatch,
    answers,
    kind=PeriodKind.WEEKLY,
):
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO("\n".join(answers) + "\n"),
    )

    creation_module.create_goals(
        environment.context,
        kind,
        environment.console,
    )

    return environment.output.getvalue()


@pytest.mark.parametrize(
    ("kind", "expected_start", "expected_end"),
    [
        (PeriodKind.WEEKLY, "2026-11-09", "2026-11-15"),
        (PeriodKind.MONTHLY, "2026-11-01", "2026-11-30"),
    ],
)
def test_confirmed_goal_is_saved_without_forcing_finalization(
    creation_environment,
    monkeypatch,
    kind,
    expected_start,
    expected_end,
):
    output = run_creation(
        creation_environment,
        monkeypatch,
        ["", *goal_answers(), "n", "n"],
        kind=kind,
    )

    goals = creation_environment.service.list_goals()

    assert len(goals) == 1
    assert goals[0]["title"] == "Learn LangChain"
    assert goals[0]["period"]["start_date"] == expected_start
    assert goals[0]["period"]["end_date"] == expected_end
    assert goals[0]["period"]["planning_closed_at"] is None
    assert goals[0]["milestones"][0]["due_date"] == expected_end
    assert output.index("Review Goal") < output.index("Save this goal?")
    assert "Goal saved." in output


def test_declined_confirmation_writes_no_goal_or_period(
    creation_environment,
    monkeypatch,
):
    output = run_creation(
        creation_environment,
        monkeypatch,
        ["", *goal_answers(save="n"), "n"],
    )

    assert creation_environment.service.list_goals() == []
    assert "Cancelled. No save was requested." in output

    with open_database(
        creation_environment.context.paths.database
    ) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM periods"
        ).fetchone()[0] == 0

        assert connection.execute(
            "SELECT COUNT(*) FROM mutation_requests"
        ).fetchone()[0] == 0


def test_initial_plan_is_finalized_only_when_confirmed(
    creation_environment,
    monkeypatch,
):
    output = run_creation(
        creation_environment,
        monkeypatch,
        ["", *goal_answers(), "n", "y"],
    )

    goal = creation_environment.service.list_goals()[0]

    assert goal["period"]["planning_closed_at"] is not None
    assert goal["is_original_plan"] is True
    assert "Initial plan finalized." in output


def test_multiple_goals_share_the_selected_period(
    creation_environment,
    monkeypatch,
):
    run_creation(
        creation_environment,
        monkeypatch,
        [
            "",
            *goal_answers("Learn LangChain"),
            "y",
            *goal_answers("Practise SQLite"),
            "n",
            "y",
        ],
    )

    goals = creation_environment.service.list_goals()

    assert {goal["title"] for goal in goals} == {
        "Learn LangChain",
        "Practise SQLite",
    }
    assert len({goal["period"]["id"] for goal in goals}) == 1
    assert all(goal["is_original_plan"] for goal in goals)
    assert all(
        goal["period"]["planning_closed_at"] is not None
        for goal in goals
    )


def test_invalid_period_inputs_prompt_again(
    creation_environment,
    monkeypatch,
):
    output = run_creation(
        creation_environment,
        monkeypatch,
        [
            "tomorrow",
            "2026-11-10",
            "2026-11-02",
            "2026-11-09",
            *goal_answers(),
            "n",
            "n",
        ],
    )

    assert "Enter a valid date" in output
    assert "must start on Monday" in output
    assert "current period or a future period" in output
    assert len(creation_environment.service.list_goals()) == 1


def test_duplicate_milestone_is_not_added(
    creation_environment,
    monkeypatch,
):
    output = run_creation(
        creation_environment,
        monkeypatch,
        [
            "",
            "Learn LangChain",
            "",
            "",
            "",
            "Complete tutorial",
            "",
            "  COMPLETE TUTORIAL  ",
            "",
            "y",
            "n",
            "n",
        ],
    )

    goal = creation_environment.service.list_goals()[0]

    assert len(goal["milestones"]) == 1
    assert "already included" in output


def test_out_of_period_milestone_deadline_prompts_again(
    creation_environment,
    monkeypatch,
):
    output = run_creation(
        creation_environment,
        monkeypatch,
        [
            "",
            "Learn LangChain",
            "",
            "",
            "",
            "Complete tutorial",
            "2026-11-16",
            "",
            "",
            "y",
            "n",
            "n",
        ],
    )

    goal = creation_environment.service.list_goals()[0]

    assert goal["milestones"][0]["due_date"] == "2026-11-15"
    assert "cannot exceed the goal deadline" in output


def test_storage_retry_reuses_request_after_uncertain_response(
    creation_environment,
    monkeypatch,
):
    service = creation_environment.service
    original_create = service.create_goal
    request_ids = []

    def uncertain_create(goal):
        request_ids.append(goal.request_id)
        result = original_create(goal)

        if len(request_ids) == 1:
            raise StorageError(
                "Simulated lost response after saving.",
                request_id=str(goal.request_id),
            )

        return result

    monkeypatch.setattr(service, "create_goal", uncertain_create)

    run_creation(
        creation_environment,
        monkeypatch,
        ["", *goal_answers(), "y", "n", "n"],
    )

    assert len(request_ids) == 2
    assert request_ids[0] == request_ids[1]
    assert len(service.list_goals()) == 1

    with open_database(service.database_path) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM mutation_requests) AS requests,
                (SELECT COUNT(*) FROM audit_events) AS audits
            """
        ).fetchone()

    assert dict(counts) == {"requests": 1, "audits": 1}