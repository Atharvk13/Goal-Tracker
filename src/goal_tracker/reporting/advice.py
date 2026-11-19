"""Optional AI report advice, shared by the terminal and web interfaces.

The flow is: gather evidence from the factual analysis, ask the model, let the
user preview the result, then save it with the evidence hash and model
provenance. ReportService checks the hash so advice is never saved against
facts that changed after the preview.
"""

from dataclasses import dataclass

from goal_tracker.db.repositories.operations import calculate_payload_hash
from goal_tracker.domain.models import PeriodSpec
from goal_tracker.llm.recommendations import Recommendation, ReportRecommendations
from goal_tracker.reporting.evidence import (
    EvidenceSelection,
    build_recommendation_evidence,
)


@dataclass(frozen=True)
class AdviceEvidence:
    """Factual snapshot and the bounded evidence sent to the model."""

    facts: dict
    selection: EvidenceSelection

    @property
    def evidence_hash(self) -> str:
        return calculate_payload_hash(self.facts)


def gather_advice_evidence(analysis_service, period: PeriodSpec) -> AdviceEvidence:
    facts = analysis_service.analyze_period(period, as_of=period.end_date)
    return AdviceEvidence(
        facts=facts,
        selection=build_recommendation_evidence(facts),
    )


def request_recommendations(
    gateway,
    evidence: AdviceEvidence,
    period: PeriodSpec,
) -> ReportRecommendations:
    return gateway.recommend_next_period(
        evidence.selection.evidence,
        period_kind=period.kind.value,
    )


ADVICE_SECTIONS = (
    ("Areas for improvement", "areas_for_improvement"),
    ("Next-period suggestions", "next_period_suggestions"),
    ("Recommended future goals", "recommended_goals"),
)


def recommendation_sections(
    recommendations: ReportRecommendations,
) -> tuple[tuple[str, list[Recommendation]], ...]:
    """Return the advice sections in display order."""
    return tuple(
        (title, getattr(recommendations, key))
        for title, key in ADVICE_SECTIONS
    )


def model_provenance(settings) -> dict:
    """Record which model configuration produced accepted advice."""
    return {
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "structured_method": settings.llm_structured_method,
    }


def completed_period_spec(period: dict) -> PeriodSpec:
    """Build a PeriodSpec from a list_completed_periods() row."""
    return PeriodSpec(
        kind=period["kind"],
        start_date=period["start_date"],
        timezone=period["timezone"],
    )
