import pytest

import goal_tracker.llm.gateway as gateway_module
from goal_tracker.config.settings import Settings
from goal_tracker.llm.schemas import ActivityProposal, ProgressProposal


class FakeExtractor:
    """Return a controlled response without contacting Ollama."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return self.result


class FakeModel:
    def __init__(self, extractor):
        self.extractor = extractor

    def with_structured_output(self, schema, **kwargs):
        return self.extractor


@pytest.fixture
def make_gateway(monkeypatch):
    def build(result):
        extractor = FakeExtractor(result)

        monkeypatch.setattr(
            gateway_module,
            "create_model",
            lambda settings: FakeModel(extractor),
        )

        settings = Settings(_env_file=None)
        gateway = gateway_module.ModelGateway(settings)
        return gateway, extractor

    return build


def test_returns_valid_proposal(make_gateway):
    text = "I completed the tutorial."
    proposal = ProgressProposal(
        activities=[
            ActivityProposal(
                description="Complete the tutorial",
                status="completed",
                evidence_quote=text,
            )
        ]
    )
    gateway, extractor = make_gateway(
        {"parsed": proposal, "parsing_error": None}
    )

    result = gateway.extract_progress(text)

    assert result == proposal
    assert extractor.calls[0][-1] == ("human", text)


def test_rejects_empty_input_before_model_call(make_gateway):
    gateway, extractor = make_gateway({})

    with pytest.raises(ValueError, match="cannot be empty"):
        gateway.extract_progress("   ")

    assert extractor.calls == []


def test_rejects_parsing_failure(make_gateway):
    gateway, _ = make_gateway(
        {
            "parsed": None,
            "parsing_error": ValueError("Invalid model output"),
        }
    )

    with pytest.raises(ValueError, match="invalid progress structure"):
        gateway.extract_progress("The exercise is pending.")


def test_rejects_missing_parsed_proposal(make_gateway):
    gateway, _ = make_gateway(
        {"parsed": None, "parsing_error": None}
    )

    with pytest.raises(ValueError, match="did not return a progress proposal"):
        gateway.extract_progress("The exercise is pending.")


def test_rejects_fabricated_evidence(make_gateway):
    proposal = ProgressProposal(
        activities=[
            ActivityProposal(
                description="Complete the exercise",
                status="completed",
                evidence_quote="I completed the exercise.",
            )
        ]
    )
    gateway, _ = make_gateway(
        {"parsed": proposal, "parsing_error": None}
    )

    with pytest.raises(ValueError, match="evidence quote"):
        gateway.extract_progress("The exercise is pending.")


def test_accepts_no_extracted_activities(make_gateway):
    proposal = ProgressProposal(activities=[])
    gateway, _ = make_gateway(
        {"parsed": proposal, "parsing_error": None}
    )

    result = gateway.extract_progress("Hello.")

    assert result.activities == []