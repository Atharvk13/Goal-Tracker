import io
from datetime import date
from unittest.mock import Mock

import pytest
from rich.console import Console

import goal_tracker.skills.loader as loader_module
from goal_tracker.cli.goal_creation import _ask_milestones
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.skills.loader import (
    SkillLoadError,
    discover_skills,
    load_skill,
)


def test_expected_bundled_skills_are_available():
    catalog = discover_skills()

    assert {item["name"] for item in catalog} == {
        "goal-planning",
        "daily-progress-recording",
        "progress-analysis",
        "weekly-report-generation",
        "monthly-report-generation",
        "daily-mail-writing",
        "goal-review",
        "week-planning",
    }
    assert all(item["description"].strip() for item in catalog)


def test_unapproved_skill_name_is_rejected():
    with pytest.raises(SkillLoadError, match="Unknown bundled Skill"):
        load_skill("../outside-skill")


def test_mismatched_metadata_name_is_rejected(
    tmp_path,
    monkeypatch,
):
    resource = (
        tmp_path
        / "skills"
        / "bundled"
        / "progress-analysis"
        / "SKILL.md"
    )
    resource.parent.mkdir(parents=True)
    resource.write_text(
        "---\n"
        "name: another-skill\n"
        "description: Example instructions.\n"
        "---\n"
        "Use recorded evidence.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        loader_module,
        "files",
        lambda package: tmp_path,
    )

    with pytest.raises(
        SkillLoadError,
        match="invalid or mismatched name",
    ):
        load_skill("progress-analysis")


@pytest.fixture
def milestone_form():
    period = PeriodSpec(
        kind="weekly",
        start_date=date(2026, 11, 16),
    )
    console = Console(
        file=io.StringIO(),
        force_terminal=False,
        width=120,
    )
    return period, console


def test_ai_milestone_can_be_reviewed_and_edited(
    monkeypatch,
    milestone_form,
):
    period, console = milestone_form
    suggester = Mock(return_value=["Complete the tutorial"])

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            "/suggest\n"
            "y\n"
            "Complete and explain the tutorial\n"
            "\n"
            "\n"
        ),
    )

    milestones = _ask_milestones(
        period,
        period.end_date,
        console,
        suggester=suggester,
    )

    suggester.assert_called_once_with()
    assert len(milestones) == 1
    assert (
        milestones[0].description
        == "Complete and explain the tutorial"
    )
    assert milestones[0].due_date is None


def test_skipped_ai_milestone_is_not_added(
    monkeypatch,
    milestone_form,
):
    period, console = milestone_form

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            "/suggest\n"
            "n\n"
            "Write a small program\n"
            "\n"
            "\n"
        ),
    )

    milestones = _ask_milestones(
        period,
        period.end_date,
        console,
        suggester=lambda: ["Complete the tutorial"],
    )

    assert [item.description for item in milestones] == [
        "Write a small program"
    ]