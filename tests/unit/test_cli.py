import os
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import goal_tracker.cli.app as cli_module
from goal_tracker.llm.capabilities import AvailabilityResult
from goal_tracker.llm.schemas import ProgressProposal


runner = CliRunner()


@pytest.fixture
def fake_services(monkeypatch):
    """Replace external services so CLI tests stay offline."""
    for name in list(os.environ):
        if name.upper().startswith("GT_"):
            monkeypatch.delenv(name)

    state = {
        "generation_calls": 0,
        "extraction_calls": 0,
        "generation_error": False,
        "extraction_error": False,
    }

    def fake_availability(settings):
        return AvailabilityResult(
            status="available",
            message="Model installed.",
            model=settings.llm_model,
            digest="test-digest",
        )

    def fake_generate(prompt):
        state["generation_calls"] += 1

        if state["generation_error"]:
            raise TimeoutError("Simulated timeout")

        return SimpleNamespace(content="Ready.")

    def fake_extract(text):
        state["extraction_calls"] += 1

        if state["extraction_error"]:
            raise ValueError("Simulated invalid extraction")

        return ProgressProposal(
            activities=[
                {
                    "description": "Complete the LangChain tutorial",
                    "status": "completed",
                    "evidence_quote": "I completed the LangChain tutorial.",
                },
                {
                    "description": "Finish the tool-calling exercise",
                    "status": "pending",
                    "evidence_quote": (
                        "The tool-calling exercise is still pending."
                    ),
                },
            ]
        )

    monkeypatch.setattr(
        cli_module,
        "check_model_availability",
        fake_availability,
    )
    monkeypatch.setattr(
        cli_module,
        "create_model",
        lambda settings: SimpleNamespace(invoke=fake_generate),
    )
    monkeypatch.setattr(
        cli_module,
        "ModelGateway",
        lambda settings: SimpleNamespace(extract_progress=fake_extract),
    )

    return state


def test_basic_doctor_skips_generation(fake_services):
    result = runner.invoke(cli_module.app, ["doctor"])

    assert result.exit_code == 0
    assert "PASS" in result.output
    assert "SKIP" in result.output
    assert fake_services["generation_calls"] == 0
    assert fake_services["extraction_calls"] == 0


def test_doctor_rejects_invalid_configuration(fake_services, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GT_LLM_TIMEOUT_SECONDS=-1\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        cli_module.app,
        ["doctor", "--env-file", str(env_file)],
    )

    assert result.exit_code == 1
    assert "Configuration" in result.output
    assert "FAIL" in result.output


@pytest.mark.parametrize(
    "status",
    ["service_unavailable", "model_missing"],
)
def test_doctor_reports_availability_failure(
    fake_services,
    monkeypatch,
    status,
):
    def unavailable(settings):
        return AvailabilityResult(
            status=status,
            message="Simulated availability failure.",
            model=settings.llm_model,
        )

    monkeypatch.setattr(
        cli_module,
        "check_model_availability",
        unavailable,
    )

    result = runner.invoke(cli_module.app, ["doctor", "--llm"])

    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert fake_services["generation_calls"] == 0


def test_live_checks_use_both_services(fake_services):
    result = runner.invoke(cli_module.app, ["doctor", "--llm"])

    assert result.exit_code == 0
    assert "Text generation" in result.output
    assert "Structured extraction" in result.output
    assert "FAIL" not in result.output
    assert fake_services["generation_calls"] == 1
    assert fake_services["extraction_calls"] == 1


def test_generation_failure_stops_extraction(fake_services):
    fake_services["generation_error"] = True

    result = runner.invoke(cli_module.app, ["doctor", "--llm"])

    assert result.exit_code == 1
    assert "TimeoutError" in result.output
    assert fake_services["extraction_calls"] == 0


def test_extraction_failure_is_reported(fake_services):
    fake_services["extraction_error"] = True

    result = runner.invoke(cli_module.app, ["doctor", "--llm"])

    assert result.exit_code == 1
    assert "Structured extraction" in result.output
    assert "ValueError" in result.output