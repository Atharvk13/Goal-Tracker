from uuid import uuid4

import pytest
from pydantic import ValidationError

from goal_tracker.domain.progress import (
    ActivityInput,
    ActivityKind,
    ActivityState,
    DailyProgressInput,
    EntrySource,
)


@pytest.fixture
def activity_data():
    return {
        "description": "Completed the tutorial",
        "state": "completed",
        "minutes": 30,
    }


@pytest.fixture
def entry_data(activity_data):
    return {
        "activity_date": "2026-11-12",
        "raw_text": "Completed the tutorial in 30 minutes.",
        "activities": [activity_data],
    }


def test_accepts_manual_entry(entry_data):
    entry = DailyProgressInput.model_validate(entry_data)

    assert entry.source == EntrySource.MANUAL
    assert entry.activities[0].kind == ActivityKind.ACTIVITY
    assert entry.activities[0].minutes == 30
    assert entry.allow_duplicate is False


def test_preserves_original_text_and_quote_whitespace():
    raw_text = "  Completed tutorial.\nLearned MCP."
    quote = "  Completed tutorial."

    entry = DailyProgressInput(
        activity_date="2026-11-12",
        raw_text=raw_text,
        source="llm_reviewed",
        activities=[
            {
                "description": "  Completed tutorial.  ",
                "state": "completed",
                "evidence_quote": quote,
            }
        ],
    )

    assert entry.raw_text == raw_text
    assert entry.activities[0].evidence_quote == quote
    assert entry.activities[0].description == "Completed tutorial."


@pytest.mark.parametrize("minutes", [-1, 1.5, True, "30"])
def test_rejects_invalid_minutes(activity_data, minutes):
    activity_data["minutes"] = minutes

    with pytest.raises(ValidationError, match="minutes"):
        ActivityInput.model_validate(activity_data)


@pytest.mark.parametrize("minutes", [0, None])
def test_accepts_zero_or_unspecified_minutes(activity_data, minutes):
    activity_data["minutes"] = minutes

    activity = ActivityInput.model_validate(activity_data)

    assert activity.minutes == minutes


def test_rejects_blank_activity_description(activity_data):
    activity_data["description"] = "   "

    with pytest.raises(ValidationError, match="cannot be blank"):
        ActivityInput.model_validate(activity_data)


def test_rejects_duplicate_goal_links(activity_data):
    goal_id = uuid4()
    activity_data["goal_ids"] = [goal_id, goal_id]

    with pytest.raises(ValidationError, match="Goal links must be unique"):
        ActivityInput.model_validate(activity_data)


def test_work_activity_requires_a_work_state(activity_data):
    activity_data["state"] = "not_applicable"

    with pytest.raises(ValidationError, match="Work activities need"):
        ActivityInput.model_validate(activity_data)


@pytest.mark.parametrize(
    "kind",
    ["learning", "challenge", "observation", "achievement"],
)
def test_non_work_items_use_not_applicable_state(kind):
    item = ActivityInput(
        kind=kind,
        description="A recorded item",
        state="not_applicable",
    )

    assert item.state == ActivityState.NOT_APPLICABLE

    with pytest.raises(ValidationError, match="not_applicable"):
        ActivityInput(
            kind=kind,
            description="A recorded item",
            state="completed",
        )


def test_activity_cannot_update_itself(activity_data):
    activity_id = uuid4()
    activity_data["id"] = activity_id
    activity_data["updates_activity_id"] = activity_id

    with pytest.raises(ValidationError, match="cannot update itself"):
        ActivityInput.model_validate(activity_data)


@pytest.mark.parametrize(
    "forbidden_field",
    ["updates_activity_id", "milestone_changes"],
)
def test_non_work_items_cannot_update_work_state(forbidden_field):
    data = {
        "kind": "learning",
        "description": "Learned about SQLite",
        "state": "not_applicable",
    }

    if forbidden_field == "updates_activity_id":
        data[forbidden_field] = uuid4()
    else:
        data[forbidden_field] = [
            {
                "milestone_id": uuid4(),
                "state": "not_done",
            }
        ]

    with pytest.raises(ValidationError):
        ActivityInput.model_validate(data)


@pytest.mark.parametrize("state", ["pending", "in_progress"])
def test_marking_milestone_done_requires_completed_activity(state):
    with pytest.raises(ValidationError, match="requires a completed activity"):
        ActivityInput(
            description="Tutorial work",
            state=state,
            milestone_changes=[
                {
                    "milestone_id": uuid4(),
                    "state": "done",
                }
            ],
        )


@pytest.mark.parametrize(
    ("activity_state", "milestone_state"),
    [
        ("completed", "done"),
        ("pending", "not_done"),
    ],
)
def test_accepts_explicit_milestone_changes(activity_state, milestone_state):
    activity = ActivityInput(
        description="Tutorial work",
        state=activity_state,
        milestone_changes=[
            {
                "milestone_id": uuid4(),
                "state": milestone_state,
            }
        ],
    )

    assert activity.milestone_changes[0].state.value == milestone_state


@pytest.mark.parametrize("raw_text", ["", " \n "])
def test_rejects_blank_entry_text(entry_data, raw_text):
    entry_data["raw_text"] = raw_text

    with pytest.raises(ValidationError, match="raw_text"):
        DailyProgressInput.model_validate(entry_data)


def test_entry_requires_at_least_one_item(entry_data):
    entry_data["activities"] = []

    with pytest.raises(ValidationError, match="activities"):
        DailyProgressInput.model_validate(entry_data)


def test_activity_ids_must_be_unique(entry_data, activity_data):
    activity_id = uuid4()
    entry_data["activities"] = [
        {**activity_data, "id": activity_id},
        {
            **activity_data,
            "id": activity_id,
            "description": "Another activity",
        },
    ]

    with pytest.raises(ValidationError, match="Activity IDs must be unique"):
        DailyProgressInput.model_validate(entry_data)


def test_existing_activity_cannot_be_updated_twice(entry_data, activity_data):
    existing_id = uuid4()
    entry_data["activities"] = [
        {**activity_data, "updates_activity_id": existing_id},
        {
            **activity_data,
            "description": "Another update",
            "updates_activity_id": existing_id,
        },
    ]

    with pytest.raises(ValidationError, match="updated twice"):
        DailyProgressInput.model_validate(entry_data)


def test_milestone_cannot_change_twice_in_an_entry(entry_data, activity_data):
    milestone_id = uuid4()
    change = {"milestone_id": milestone_id, "state": "done"}

    entry_data["activities"] = [
        {**activity_data, "milestone_changes": [change]},
        {
            **activity_data,
            "description": "Another completed activity",
            "milestone_changes": [change],
        },
    ]

    with pytest.raises(ValidationError, match="cannot change twice"):
        DailyProgressInput.model_validate(entry_data)


def test_ai_interpreted_items_require_quotes(entry_data):
    entry_data["source"] = "llm_reviewed"

    with pytest.raises(ValidationError, match="supporting quote"):
        DailyProgressInput.model_validate(entry_data)


@pytest.mark.parametrize("source", ["manual", "llm_reviewed"])
def test_supplied_quote_must_exist_in_original_text(
    entry_data,
    source,
):
    entry_data["source"] = source
    entry_data["activities"][0]["evidence_quote"] = "I finished everything."

    with pytest.raises(ValidationError, match="not found"):
        DailyProgressInput.model_validate(entry_data)


def test_correction_requires_reason(entry_data):
    entry_data["supersedes_entry_id"] = uuid4()

    with pytest.raises(ValidationError, match="requires a reason"):
        DailyProgressInput.model_validate(entry_data)


def test_correction_reason_cannot_be_blank(entry_data):
    entry_data["supersedes_entry_id"] = uuid4()
    entry_data["correction_reason"] = "   "

    with pytest.raises(ValidationError, match="correction_reason"):
        DailyProgressInput.model_validate(entry_data)


def test_accepts_correction_with_reason(entry_data):
    original_entry_id = uuid4()
    entry_data["supersedes_entry_id"] = original_entry_id
    entry_data["correction_reason"] = "  Correct the recorded duration.  "

    entry = DailyProgressInput.model_validate(entry_data)

    assert entry.supersedes_entry_id == original_entry_id
    assert entry.correction_reason == "Correct the recorded duration."


def test_duplicate_override_requires_boolean(entry_data):
    entry_data["allow_duplicate"] = "yes"

    with pytest.raises(ValidationError, match="allow_duplicate"):
        DailyProgressInput.model_validate(entry_data)


def test_json_round_trip_preserves_request_and_activity_ids(entry_data):
    original = DailyProgressInput.model_validate(entry_data)

    restored = DailyProgressInput.model_validate_json(
        original.model_dump_json()
    )

    assert restored.request_id == original.request_id
    assert restored.activities[0].id == original.activities[0].id
    assert restored.raw_text == original.raw_text
    assert restored.model_dump() == original.model_dump()