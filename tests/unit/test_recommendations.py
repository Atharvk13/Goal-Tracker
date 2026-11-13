from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from goal_tracker.llm import gateway as gateway_module
from goal_tracker.llm.recommendations import (
    Recommendation,
    ReportRecommendations,
)


@pytest.fixture
def simulated_gateway(monkeypatch):
    """Replace the actual model with a simulated structured response."""
    model = Mock()
    structured_model = Mock()
    model.with_structured_output.return_value = structured_model

    monkeypatch.setattr(
        gateway_module,
        "create_model",
        lambda settings: model,
    )

    settings = SimpleNamespace(
        llm_structured_method="json_schema",
    )
    gateway = gateway_module.ModelGateway(settings)

    return gateway, structured_model


def _recommendations(evidence_id: str) -> ReportRecommendations:
    return ReportRecommendations(
        areas_for_improvement=[
            Recommendation(
                text="Practise tool calling with a small exercise.",
                evidence_ids=[evidence_id],
            )
        ],
        next_period_suggestions=[],
        recommended_goals=[],
    )


def test_known_evidence_is_accepted(simulated_gateway):
    gateway, model = simulated_gateway
    proposed = _recommendations("activity:recorded-challenge")

    model.invoke.return_value = {
        "parsing_error": None,
        "parsed": proposed,
    }

    result = gateway.recommend_next_period(
        {
            "activity:recorded-challenge": (
                "Could not finish the tool-calling exercise."
            )
        }
    )

    assert result == proposed


def test_invented_evidence_is_rejected(simulated_gateway):
    gateway, model = simulated_gateway

    model.invoke.return_value = {
        "parsing_error": None,
        "parsed": _recommendations("activity:invented-record"),
    }

    with pytest.raises(ValueError, match="unknown evidence"):
        gateway.recommend_next_period(
            {
                "activity:recorded-challenge": (
                    "Could not finish the tool-calling exercise."
                )
            }
        )


def test_malformed_model_output_is_rejected(simulated_gateway):
    gateway, model = simulated_gateway

    model.invoke.return_value = {
        "parsing_error": ValueError("Invalid structured response"),
        "parsed": None,
    }

    with pytest.raises(
        ValueError,
        match="invalid recommendation structure",
    ):
        gateway.recommend_next_period(
            {"activity:challenge": "The exercise was difficult."}
        )


def test_empty_evidence_does_not_call_model(simulated_gateway):
    gateway, model = simulated_gateway

    result = gateway.recommend_next_period({})

    assert result.areas_for_improvement == []
    assert result.next_period_suggestions == []
    assert result.recommended_goals == []
    model.invoke.assert_not_called()


def test_blank_evidence_reference_is_rejected():
    with pytest.raises(ValidationError):
        Recommendation(
            text="Practise tool calling.",
            evidence_ids=["   "],
        )