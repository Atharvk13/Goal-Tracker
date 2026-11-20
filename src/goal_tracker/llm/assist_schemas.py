"""Structured output for goal suggestions and the weekly plan.

Every field is required: the small local model tends to leave optional
fields empty.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

Weekday = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def clean_items(values: list[str]) -> list[str]:
    """Strip items, drop blanks and case-insensitive repeats."""
    cleaned, seen = [], set()

    for value in values:
        text = value.strip()[:300]
        if text and text.casefold() not in seen:
            cleaned.append(text)
            seen.add(text.casefold())

    return cleaned


class GoalReview(BaseModel):
    """Suggestions shown beside a saved goal."""

    model_config = ConfigDict(extra="forbid")

    milestone_ideas: list[str] = Field(
        max_length=5,
        description="Measurable milestones missing from the plan.",
    )
    tips: list[str] = Field(
        max_length=5,
        description="Short, practical tips for working on this goal.",
    )
    risks: list[str] = Field(
        max_length=3,
        description="Realistic risks, each with a short way to avoid it.",
    )

    @field_validator("milestone_ideas", "tips", "risks")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return clean_items(values)


class DayPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: Weekday = Field(description="A working day from the supplied list.")
    focus: str = Field(
        max_length=200,
        description="The main focus of the day in a few words.",
    )
    tasks: list[str] = Field(
        max_length=6,
        description="Tasks for the day, taken from the supplied work.",
    )

    @field_validator("tasks")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return clean_items(values)


class WeekPlan(BaseModel):
    """A Monday-to-Friday plan; advice only."""

    model_config = ConfigDict(extra="forbid")

    days: list[DayPlan] = Field(
        max_length=5,
        description="One entry per supplied working day, in order.",
    )
    notes: list[str] = Field(
        max_length=3,
        description="Short tips about the week as a whole.",
    )

    @field_validator("notes")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return clean_items(values)
