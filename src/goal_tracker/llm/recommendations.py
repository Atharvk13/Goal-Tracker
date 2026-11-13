from pydantic import BaseModel, ConfigDict, Field, field_validator


class Recommendation(BaseModel):
    """One advisory suggestion linked to recorded evidence."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    text: str = Field(
        min_length=1,
        max_length=800,
        description=(
            "An actionable suggestion supported by the referenced evidence."
        ),
    )

    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=8,
        description=(
            "Exact evidence IDs supplied in the model's input context."
        ),
    )

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]

        if any(not value for value in cleaned):
            raise ValueError("Evidence IDs cannot be blank.")

        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Evidence IDs cannot be repeated.")

        return cleaned


class ReportRecommendations(BaseModel):
    """Advisory sections for a weekly or monthly report."""

    model_config = ConfigDict(extra="forbid")

    areas_for_improvement: list[Recommendation] = Field(
        max_length=5,
        description=(
            "Suggested improvements based on recorded challenges "
            "or other supplied evidence."
        ),
    )

    next_period_suggestions: list[Recommendation] = Field(
        max_length=5,
        description="Practical actions to consider for the next period.",
    )

    recommended_goals: list[Recommendation] = Field(
        max_length=3,
        description=(
            "Proposed future goals. These are suggestions, "
            "not goals to create automatically."
        ),
    )