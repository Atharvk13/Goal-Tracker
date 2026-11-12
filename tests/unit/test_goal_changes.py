from uuid import uuid4

import pytest
from pydantic import ValidationError

from goal_tracker.domain.goal_changes import (
    GoalLifecycleChange,
    GoalUpdate,
    MilestoneChange,
    PlanFinalize,
)


@pytest.fixture
def update_input():
    return {
        "goal_id": uuid4(),
        "expected_revision": 1,
        "reason": "Clarify the expected outcome.",
    }


def test_accepts_title_change(update_input):
    change = GoalUpdate(
        **update_input,
        title="  Build a LangChain project  ",
    )

    assert change.title == "Build a LangChain project"
    assert change.expected_revision == 1


def test_requires_an_actual_change(update_input):
    with pytest.raises(ValidationError, match="goal change is required"):
        GoalUpdate(**update_input)


@pytest.mark.parametrize("revision", [0, -1])
def test_rejects_invalid_expected_revision(update_input, revision):
    update_input["expected_revision"] = revision

    with pytest.raises(ValidationError, match="expected_revision"):
        GoalUpdate(**update_input, title="Updated title")


def test_rejects_blank_reason(update_input):
    update_input["reason"] = "   "

    with pytest.raises(ValidationError, match="reason"):
        GoalUpdate(**update_input, title="Updated title")


@pytest.mark.parametrize(
    "field",
    ["title", "description", "expected_outcome"],
)
def test_rejects_null_text_changes(update_input, field):
    data = {
        **update_input,
        "title": "Updated title",
    }
    data[field] = None

    with pytest.raises(ValidationError, match="cannot be null"):
        GoalUpdate.model_validate(data)


def test_distinguishes_omitted_deadline_from_explicit_clear(update_input):
    unchanged_deadline = GoalUpdate(
        **update_input,
        title="Updated title",
    )
    cleared_deadline = GoalUpdate(
        **update_input,
        target_date=None,
    )

    assert unchanged_deadline.target_date is None
    assert cleared_deadline.target_date is None

    assert "target_date" not in unchanged_deadline.model_fields_set
    assert "target_date" in cleared_deadline.model_fields_set


def test_milestone_change_requires_an_edit():
    with pytest.raises(ValidationError, match="milestone change is required"):
        MilestoneChange(milestone_id=uuid4())


def test_milestone_deadline_can_be_explicitly_cleared():
    change = MilestoneChange(
        milestone_id=uuid4(),
        due_date=None,
    )

    assert change.due_date is None
    assert "due_date" in change.model_fields_set


@pytest.mark.parametrize("field", ["description", "is_included"])
def test_rejects_null_milestone_changes(field):
    data = {
        "milestone_id": uuid4(),
        field: None,
    }

    with pytest.raises(ValidationError, match="cannot be null"):
        MilestoneChange.model_validate(data)


def test_rejects_repeated_changes_to_same_milestone(update_input):
    milestone_id = uuid4()

    with pytest.raises(ValidationError, match="changed twice"):
        GoalUpdate(
            **update_input,
            milestone_changes=[
                {
                    "milestone_id": milestone_id,
                    "description": "Updated description",
                },
                {
                    "milestone_id": milestone_id,
                    "is_included": False,
                },
            ],
        )


def test_rejects_duplicate_new_milestones(update_input):
    with pytest.raises(ValidationError, match="must be unique"):
        GoalUpdate(
            **update_input,
            add_milestones=[
                {"description": "Build a client"},
                {"description": "  BUILD A CLIENT  "},
            ],
        )


def test_accepts_milestone_only_edits(update_input):
    addition = GoalUpdate(
        **update_input,
        add_milestones=[
            {"description": "Build a client"},
        ],
    )
    modification = GoalUpdate(
        **update_input,
        milestone_changes=[
            {
                "milestone_id": uuid4(),
                "is_included": False,
            }
        ],
    )

    assert len(addition.add_milestones) == 1
    assert len(modification.milestone_changes) == 1


@pytest.mark.parametrize("action", ["cancel", "archive"])
def test_accepts_supported_lifecycle_actions(update_input, action):
    change = GoalLifecycleChange(
        **update_input,
        action=action,
    )

    assert change.action == action


def test_rejects_unsupported_lifecycle_action(update_input):
    with pytest.raises(ValidationError, match="action"):
        GoalLifecycleChange(
            **update_input,
            action="restore",
        )


def test_plan_finalization_has_a_default_reason():
    request = PlanFinalize(period_id=uuid4())

    assert request.reason == "Initial planning completed."
    assert request.request_id is not None


def test_patch_serialization_preserves_intent_and_request_id(update_input):
    original = GoalUpdate(
        **update_input,
        title="Updated title",
        milestone_changes=[
            {
                "milestone_id": uuid4(),
                "due_date": None,
            }
        ],
    )

    payload = original.model_dump(
        mode="json",
        exclude_unset=True,
    )
    payload["request_id"] = str(original.request_id)

    restored = GoalUpdate.model_validate(payload)

    assert restored.request_id == original.request_id
    assert "target_date" not in restored.model_fields_set
    assert restored.milestone_changes[0].model_fields_set == {
        "milestone_id",
        "due_date",
    }