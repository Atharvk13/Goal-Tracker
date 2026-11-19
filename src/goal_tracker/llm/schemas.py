from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActivityProposal(BaseModel):
    """One work activity or journal item proposed for review."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "activity",
        "learning",
        "challenge",
        "observation",
        "achievement",
    ] = Field(
        default="activity",
        description=(
            "Classify the explicitly stated information as work, "
            "a learning, a challenge, an observation or an achievement."
        ),
    )

    description: str = Field(
        min_length=1,
        description="A brief description supported by the source quote.",
    )

    status: Literal[
        "completed",
        "pending",
        "in_progress",
        "not_applicable",
    ] = Field(
        description=(
            "For work activities, use the explicitly stated status. "
            "For all other kinds, use not_applicable."
        ),
    )

    evidence_quote: str = Field(
        min_length=1,
        description=(
            "An exact quote from the user's update supporting this item."
        ),
    )

    @model_validator(mode="after")
    def validate_kind_and_status(self) -> "ActivityProposal":
        if self.kind == "activity":
            if self.status == "not_applicable":
                raise ValueError(
                    "Work activities require an explicit work status."
                )

        elif self.status != "not_applicable":
            raise ValueError(
                "Non-work journal items must use not_applicable."
            )

        return self


_Bullets = list[str]


class DailyMailDraft(BaseModel):
    """Bullet points for each section of the daily status mail."""

    model_config = ConfigDict(extra="forbid")

    meetings_activities: _Bullets = Field(
        max_length=12,
        description="Meetings, discussions and team activities, one per bullet.",
    )
    use_cases_completed: _Bullets = Field(
        max_length=12,
        description="Use cases, features or tasks worked on or completed.",
    )
    learning: _Bullets = Field(
        max_length=12,
        description="Technologies, concepts or skills learned or explored.",
    )
    plan_for_tomorrow: _Bullets = Field(
        max_length=12,
        description="Planned tasks and next steps for tomorrow.",
    )
    blockers: _Bullets = Field(
        max_length=12,
        description="Blockers or support required.",
    )


class ProgressProposal(BaseModel):
    """Proposed journal items for user review before saving."""

    model_config = ConfigDict(extra="forbid")

    activities: list[ActivityProposal] = Field(
        description=(
            "Explicitly stated work activities, learnings, challenges, "
            "observations and achievements."
        ),
    )