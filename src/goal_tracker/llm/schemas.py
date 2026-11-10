from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ActivityProposal(BaseModel):
    """One activity extracted from a user's update."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(
        min_length=1,
        description="A brief description of the activity.",
    )
    status: Literal["completed", "pending", "in_progress"] = Field(
        description="The activity's explicitly stated status.",
    )
    evidence_quote: str = Field(
        min_length=1,
        description="An exact quote from the user's update supporting this activity.",
    )


class ProgressProposal(BaseModel):
    """Proposed activities for user review before saving."""

    model_config = ConfigDict(extra="forbid")

    activities: list[ActivityProposal] = Field(
        description="Activities explicitly mentioned in the user's update.",
    )
    