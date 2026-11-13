from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, field_validator, model_validator

from goal_tracker.domain.enums import MilestoneState
from goal_tracker.domain.models import DomainModel


class ActivityKind(StrEnum):
    ACTIVITY = "activity"
    LEARNING = "learning"
    CHALLENGE = "challenge"
    OBSERVATION = "observation"
    ACHIEVEMENT = "achievement"


class ActivityState(StrEnum):
    COMPLETED = "completed"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    NOT_APPLICABLE = "not_applicable"


class EntrySource(StrEnum):
    MANUAL = "manual"
    LLM_REVIEWED = "llm_reviewed"


class ProgressModel(DomainModel):
    """Preserve original journal text and supporting quotes."""

    model_config = ConfigDict(
        str_strip_whitespace=False,
    )


class MilestoneProgress(DomainModel):
    """An explicitly reviewed milestone state change."""

    milestone_id: UUID
    state: MilestoneState


class ActivityInput(ProgressModel):
    """One recorded activity, learning, challenge or observation."""

    id: UUID = Field(default_factory=uuid4)
    kind: ActivityKind = ActivityKind.ACTIVITY
    description: str = Field(min_length=1)
    state: ActivityState
    minutes: int | None = Field(default=None, ge=0, strict=True)

    goal_ids: list[UUID] = Field(default_factory=list)
    evidence_quote: str | None = Field(default=None, min_length=1)
    updates_activity_id: UUID | None = None
    milestone_changes: list[MilestoneProgress] = Field(default_factory=list)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        cleaned = value.strip()

        if not cleaned:
            raise ValueError("The activity description cannot be blank.")

        return cleaned

    @field_validator("evidence_quote")
    @classmethod
    def validate_quote(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("An evidence quote cannot be blank.")

        return value

    @field_validator("goal_ids")
    @classmethod
    def validate_goal_links(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("Goal links must be unique within an activity.")

        return values

    @model_validator(mode="after")
    def validate_activity(self) -> Self:
        is_work = self.kind == ActivityKind.ACTIVITY

        if is_work and self.state == ActivityState.NOT_APPLICABLE:
            raise ValueError(
                "Work activities need a completed, pending or in-progress state."
            )

        if not is_work and self.state != ActivityState.NOT_APPLICABLE:
            raise ValueError(
                "Learnings, challenges, observations and achievements "
                "use the not_applicable state."
            )

        if self.updates_activity_id == self.id:
            raise ValueError("An activity cannot update itself.")

        if self.updates_activity_id is not None and not is_work:
            raise ValueError(
                "Only work activities can update an existing activity."
            )

        if self.milestone_changes and not is_work:
            raise ValueError(
                "Milestone changes must be supported by a work activity."
            )

        for change in self.milestone_changes:
            if (
                change.state == MilestoneState.DONE
                and self.state != ActivityState.COMPLETED
            ):
                raise ValueError(
                    "Marking a milestone done requires a completed activity."
                )

        return self


class DailyProgressInput(ProgressModel):
    """A reviewed daily entry ready for service-level validation."""

    activity_date: date
    raw_text: str = Field(min_length=1)
    activities: list[ActivityInput] = Field(min_length=1)

    source: EntrySource = EntrySource.MANUAL
    allow_duplicate: bool = Field(default=False, strict=True)
    supersedes_entry_id: UUID | None = None
    correction_reason: str | None = Field(default=None, min_length=1)
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator("raw_text")
    @classmethod
    def validate_raw_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("The daily entry cannot be blank.")

        return value

    @field_validator("correction_reason")
    @classmethod
    def validate_correction_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None

        cleaned = value.strip()

        if not cleaned:
            raise ValueError("A correction reason cannot be blank.")

        return cleaned

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        if (
            self.supersedes_entry_id is not None
            and self.correction_reason is None
        ):
            raise ValueError("Correcting an entry requires a reason.")

        activity_ids = set()
        updated_activity_ids = set()
        changed_milestone_ids = set()

        for activity in self.activities:
            if activity.id in activity_ids:
                raise ValueError("Activity IDs must be unique within an entry.")

            activity_ids.add(activity.id)

            if activity.updates_activity_id is not None:
                if activity.updates_activity_id in updated_activity_ids:
                    raise ValueError(
                        "An existing activity cannot be updated twice "
                        "in one entry."
                    )

                updated_activity_ids.add(activity.updates_activity_id)

            quote = activity.evidence_quote

            if self.source == EntrySource.LLM_REVIEWED and quote is None:
                raise ValueError(
                    "Every AI-interpreted item requires a supporting quote."
                )

            if quote is not None and quote not in self.raw_text:
                raise ValueError(
                    "An evidence quote was not found in the original entry."
                )

            for change in activity.milestone_changes:
                if change.milestone_id in changed_milestone_ids:
                    raise ValueError(
                        "A milestone cannot change twice in one entry."
                    )

                changed_milestone_ids.add(change.milestone_id)

        return self