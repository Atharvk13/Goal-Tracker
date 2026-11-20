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


class ChatAnswer(BaseModel):
    """A reply grounded in supplied records."""

    model_config = ConfigDict(extra="forbid")

    answered: bool = Field(
        description="True only when the supplied records contain the answer."
    )
    answer: str = Field(
        max_length=2000,
        description="The answer, using only the supplied records.",
    )
    sources: list[str] = Field(
        max_length=20,
        description="IDs of the records the answer uses, such as G1 or A3.",
    )


AGENT_INTENTS = (
    "question",
    "update_work_status",
    "log_work",
    "complete_milestone",
    "add_milestone",
    "move_milestone",
    "none",
)


class AgentDecision(BaseModel):
    """One action Jarvis proposes from a chat message (confirmed later)."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal[
        "question",
        "update_work_status",
        "log_work",
        "complete_milestone",
        "add_milestone",
        "move_milestone",
        "none",
    ] = Field(description="What the user asks for.")
    target: str = Field(
        max_length=20,
        description="ID of the record the action is about (G#, M# or P#), or empty.",
    )
    state: Literal["completed", "in_progress", "pending", "none"] = Field(
        description="New work status, or none."
    )
    text: str = Field(
        max_length=300,
        description="Work description or new milestone text, or empty.",
    )
    minutes: int = Field(ge=-1, le=1440, description="Minutes spent, or -1 if not stated.")
    date: str = Field(
        max_length=10,
        description="A date in YYYY-MM-DD format if the user gave one, else empty.",
    )


class WeekRecap(BaseModel):
    """A short, records-only recap of the working week."""

    model_config = ConfigDict(extra="forbid")

    points: list[str] = Field(
        max_length=5,
        description="Up to five short points about the week, from the records only.",
    )

    @field_validator("points")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        return clean_items(values)


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
