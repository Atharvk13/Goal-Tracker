from calendar import monthrange
from datetime import date, timedelta
from typing import Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from goal_tracker.domain.enums import PeriodKind


class DomainModel(BaseModel):
    """Common validation rules for application inputs."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class PeriodSpec(DomainModel):
    """A calendar week or month in a specified timezone."""

    kind: PeriodKind
    start_date: date
    timezone: str = "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(
                "Timezone must be a valid IANA timezone name."
            ) from error

        return value

    @model_validator(mode="after")
    def validate_calendar_start(self) -> Self:
        if self.kind == PeriodKind.WEEKLY:
            if self.start_date.weekday() != 0:
                raise ValueError("A weekly period must start on Monday.")

        elif self.start_date.day != 1:
            raise ValueError("A monthly period must start on the first day.")

        return self

    @property
    def end_date(self) -> date:
        if self.kind == PeriodKind.WEEKLY:
            return self.start_date + timedelta(days=6)

        last_day = monthrange(
            self.start_date.year,
            self.start_date.month,
        )[1]

        return self.start_date.replace(day=last_day)


class MilestoneInput(DomainModel):
    """One measurable checklist item."""

    description: str = Field(min_length=1)
    due_date: date | None = None


class GoalCreate(DomainModel):
    """Validated input for creating a goal and its milestones."""

    period: PeriodSpec
    title: str = Field(min_length=1)
    description: str = ""
    expected_outcome: str = ""
    target_date: date | None = None
    milestones: list[MilestoneInput] = Field(min_length=1)
    predecessor_goal_id: UUID | None = None
    request_id: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def validate_goal_plan(self) -> Self:
        period_start = self.period.start_date
        period_end = self.period.end_date

        if self.target_date is not None:
            if not period_start <= self.target_date <= period_end:
                raise ValueError(
                    "The goal target date must fall within its period."
                )

        deadline = self.target_date or period_end
        descriptions = set()

        for milestone in self.milestones:
            normalized_description = milestone.description.casefold()

            if normalized_description in descriptions:
                raise ValueError(
                    "Milestone descriptions must be unique within a goal."
                )

            descriptions.add(normalized_description)

            if milestone.due_date is not None:
                if not period_start <= milestone.due_date <= deadline:
                    raise ValueError(
                        "Milestone due dates must fall within the period "
                        "and cannot exceed the goal deadline."
                    )

        return self