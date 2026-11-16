import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from rich.console import Console

import goal_tracker.cli.progress as progress_module
from goal_tracker.llm.schemas import ActivityProposal, ProgressProposal


@pytest.mark.parametrize(
    "kind",
    ["learning", "challenge", "observation", "achievement"],
)
def test_non_work_categories_accept_not_applicable(kind):
    item = ActivityProposal(
        kind=kind,
        description="Explicitly recorded journal item",
        status="not_applicable",
        evidence_quote="Explicitly recorded journal item.",
    )

    assert item.kind == kind
    assert item.status == "not_applicable"


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("activity", "not_applicable"),
        ("learning", "completed"),
    ],
)
def test_incompatible_category_and_status_are_rejected(
    kind,
    status,
):
    with pytest.raises(ValidationError):
        ActivityProposal(
            kind=kind,
            description="Recorded item",
            status=status,
            evidence_quote="Recorded item.",
        )


def test_reviewed_non_work_items_do_not_change_milestones(
    monkeypatch,
):
    raw_text = (
        "I learned structured output. "
        "I struggled with tool calling."
    )

    proposal = ProgressProposal(
        activities=[
            ActivityProposal(
                kind="learning",
                description="Learned structured output",
                status="not_applicable",
                evidence_quote="I learned structured output.",
            ),
            ActivityProposal(
                kind="challenge",
                description="Struggled with tool calling",
                status="not_applicable",
                evidence_quote="I struggled with tool calling.",
            ),
        ]
    )

    gateway = Mock()
    gateway.extract_progress.return_value = proposal

    monkeypatch.setattr(
        progress_module,
        "ModelGateway",
        lambda settings: gateway,
    )
    monkeypatch.setattr(
        progress_module,
        "_select_goals",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        progress_module,
        "_ask_minutes",
        Mock(return_value=None),
    )

    milestone_selector = Mock()
    monkeypatch.setattr(
        progress_module,
        "_select_milestone_changes",
        milestone_selector,
    )

    # For each item: include, keep category, keep description.
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO("\n\n\n\n\n\n"),
    )

    context = SimpleNamespace(
        settings=SimpleNamespace(),
        logger=Mock(),
    )
    console = Console(
        file=io.StringIO(),
        force_terminal=False,
    )

    items = progress_module._review_ai_activities(
        context,
        raw_text,
        [],
        console,
    )

    assert items is not None
    assert [item.kind.value for item in items] == [
        "learning",
        "challenge",
    ]
    assert all(
        item.state.value == "not_applicable"
        for item in items
    )
    assert all(not item.milestone_changes for item in items)

    milestone_selector.assert_not_called()