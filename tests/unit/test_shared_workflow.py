"""Shared workflow helpers used by both the terminal and the web interface."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from goal_tracker.db.repositories.operations import calculate_payload_hash
from goal_tracker.domain.enums import PeriodKind
from goal_tracker.domain.periods import current_period_start
from goal_tracker.domain.progress import ActivityState, DailyProgressInput
from goal_tracker.llm.recommendations import ReportRecommendations
from goal_tracker.progress import workflow
from goal_tracker.reporting import advice
from goal_tracker.reporting.evidence import EvidenceSelection


@pytest.mark.parametrize(
    ("kind", "today", "expected"),
    [
        (PeriodKind.WEEKLY, date(2026, 11, 19), date(2026, 11, 16)),
        ("weekly", date(2026, 11, 16), date(2026, 11, 16)),
        (PeriodKind.MONTHLY, date(2026, 11, 19), date(2026, 11, 1)),
        ("monthly", date(2026, 12, 1), date(2026, 12, 1)),
    ],
)
def test_current_period_start(kind, today, expected):
    assert current_period_start(kind, today) == expected


def test_milestone_candidates_skip_removed_and_used_milestones():
    goals = [
        {
            "id": "g1",
            "milestones": [
                {"id": "m1", "is_included": True},
                {"id": "m2", "is_included": False},
                {"id": "m3", "is_included": True},
            ],
        }
    ]

    candidates = workflow.milestone_candidates(goals, {"m3"})

    assert [milestone["id"] for _, milestone in candidates] == ["m1"]


def test_only_completed_work_can_mark_milestones_done():
    assert workflow.milestone_state_choices(ActivityState.COMPLETED) == [
        "done",
        "not_done",
    ]

    for state in (ActivityState.PENDING, ActivityState.IN_PROGRESS):
        assert workflow.milestone_state_choices(state) == ["not_done"]


def test_allow_duplicate_keeps_draft_contents():
    draft = DailyProgressInput(
        activity_date=date(2026, 11, 18),
        raw_text="Built the review page.",
        activities=[{"description": "Built the review page.", "state": "completed"}],
    )

    confirmed = workflow.allow_duplicate(draft)

    assert confirmed.allow_duplicate is True
    assert confirmed.model_dump(exclude={"allow_duplicate"}) == draft.model_dump(
        exclude={"allow_duplicate"}
    )


def test_extraction_failure_is_logged_and_reported():
    gateway = Mock()
    gateway.extract_progress.side_effect = ConnectionError("offline")
    logger = Mock()

    with pytest.raises(workflow.ExtractionUnavailable):
        workflow.extract_journal_items(gateway, "text", logger)

    logger.warning.assert_called_once_with(
        "Progress extraction failed (%s).",
        "ConnectionError",
    )


def test_extraction_returns_proposed_items():
    items = [SimpleNamespace(description="one")]
    gateway = Mock()
    gateway.extract_progress.return_value = SimpleNamespace(activities=items)

    assert workflow.extract_journal_items(gateway, "text", Mock()) == items


def test_load_all_pending_reads_every_page():
    pages = [[{"id": n} for n in range(100)], [{"id": 100}]]
    service = Mock()
    service.list_pending_activities.side_effect = pages

    pending = workflow.load_all_pending(service, date(2026, 11, 19))

    assert len(pending) == 101
    assert service.list_pending_activities.call_args_list[1].kwargs["offset"] == 100


def test_updatable_pending_requires_every_linked_goal():
    pending = [
        {"id": "a", "goal_ids": ["g1"]},
        {"id": "b", "goal_ids": ["g1", "g2"]},
        {"id": "c", "goal_ids": []},
    ]

    kept = workflow.updatable_pending(pending, {"g1": {}})

    assert [activity["id"] for activity in kept] == ["a", "c"]


def test_advice_evidence_hash_matches_report_check():
    facts = {"period": {"id": "p1"}, "goals": []}
    evidence = advice.AdviceEvidence(
        facts=facts,
        selection=EvidenceSelection(evidence={}, omitted_count=0),
    )

    assert evidence.evidence_hash == calculate_payload_hash(facts)


def test_recommendation_sections_keep_display_order():
    recommendations = ReportRecommendations(
        areas_for_improvement=[],
        next_period_suggestions=[],
        recommended_goals=[],
    )

    titles = [title for title, _ in advice.recommendation_sections(recommendations)]

    assert titles == [
        "Areas for improvement",
        "Next-period suggestions",
        "Recommended future goals",
    ]


def test_model_provenance_and_completed_period_spec():
    settings = SimpleNamespace(
        llm_provider="ollama",
        llm_model="test-model",
        llm_temperature=0.1,
        llm_structured_method="json_schema",
    )

    assert advice.model_provenance(settings) == {
        "provider": "ollama",
        "model": "test-model",
        "temperature": 0.1,
        "structured_method": "json_schema",
    }

    period = advice.completed_period_spec(
        {
            "id": "p1",
            "kind": "weekly",
            "start_date": "2026-11-09",
            "timezone": "Asia/Kolkata",
        }
    )

    assert period.kind == PeriodKind.WEEKLY
    assert period.end_date == date(2026, 11, 15)
