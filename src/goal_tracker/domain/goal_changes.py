from datetime import date
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from goal_tracker.domain.models import DomainModel, MilestoneInput


class MilestoneChange(DomainModel):
    """Requested changes to an existing milestone."""

    milestone_id: UUID
    description: str | None = Field(default=None, min_length=1)
    due_date: date | None = None
    is_included: bool | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        changed_fields = self.model_fields_set - {"milestone_id"}

        if not changed_fields:
            raise ValueError("At least one milestone change is required.")

        for name in ("description", "is_included"):
            if name in changed_fields and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")

        return self


class GoalUpdate(DomainModel):
    """Requested edits with optimistic revision checking."""

    goal_id: UUID
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1)
    effective_date: date | None = None

    title: str | None = Field(default=None, min_length=1)
    description: str | None = None
    expected_outcome: str | None = None
    target_date: date | None = None

    add_milestones: list[MilestoneInput] = Field(default_factory=list)
    milestone_changes: list[MilestoneChange] = Field(default_factory=list)
    request_id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        editable_fields = {
            "title",
            "description",
            "expected_outcome",
            "target_date",
        }
        changed_fields = self.model_fields_set & editable_fields

        if (
            not changed_fields
            and not self.add_milestones
            and not self.milestone_changes
        ):
            raise ValueError("At least one goal change is required.")

        for name in ("title", "description", "expected_outcome"):
            if name in changed_fields and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null.")

        changed_ids = [
            change.milestone_id
            for change in self.milestone_changes
        ]

        if len(changed_ids) != len(set(changed_ids)):
            raise ValueError(
                "A milestone cannot be changed twice in one request."
            )

        descriptions = [
            milestone.description.casefold()
            for milestone in self.add_milestones
        ]

        if len(descriptions) != len(set(descriptions)):
            raise ValueError(
                "New milestone descriptions must be unique."
            )

        return self


class GoalLifecycleChange(DomainModel):
    """A reviewed request to cancel or archive a goal."""

    goal_id: UUID
    expected_revision: int = Field(ge=1)
    action: Literal["cancel", "archive"]
    reason: str = Field(min_length=1)
    effective_date: date | None = None
    request_id: UUID = Field(default_factory=uuid4)


class PlanFinalize(DomainModel):
    """Close initial planning so later goals are identified as additions."""

    period_id: UUID
    reason: str = Field(
        default="Initial planning completed.",
        min_length=1,
    )
    request_id: UUID = Field(default_factory=uuid4)