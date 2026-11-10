import io
import json
from urllib.error import HTTPError, URLError

import pytest

import goal_tracker.llm.capabilities as capabilities_module
from goal_tracker.config.settings import Settings


@pytest.fixture
def settings():
    """Use explicit values so the tests ignore local model configuration."""
    return Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_model="test-model",
        ollama_base_url="http://127.0.0.1:11434",
        llm_temperature=0,
        llm_timeout_seconds=120,
        llm_structured_method="json_schema",
        timezone="Asia/Kolkata",
    )


def mock_response(monkeypatch, payload):
    """Replace the HTTP request with an in-memory JSON response."""
    body = json.dumps(payload).encode("utf-8")

    def fake_urlopen(url, timeout):
        return io.BytesIO(body)

    monkeypatch.setattr(capabilities_module, "urlopen", fake_urlopen)


def test_finds_configured_model(monkeypatch, settings):
    mock_response(
        monkeypatch,
        {
            "models": [
                {"name": "test-model", "digest": "example-digest"}
            ]
        },
    )

    result = capabilities_module.check_model_availability(settings)

    assert result.status == "available"
    assert result.model == "test-model"
    assert result.digest == "example-digest"


def test_reports_missing_model(monkeypatch, settings):
    mock_response(
        monkeypatch,
        {"models": [{"name": "different-model"}]},
    )

    result = capabilities_module.check_model_availability(settings)

    assert result.status == "model_missing"
    assert result.digest is None


@pytest.mark.parametrize(
    "error",
    [
        URLError("Connection refused"),
        TimeoutError("Request timed out"),
        HTTPError(
            url="http://127.0.0.1:11434/api/tags",
            code=503,
            msg="Service unavailable",
            hdrs=None,
            fp=None,
        ),
    ],
)
def test_handles_service_failure(monkeypatch, settings, error):
    def fake_urlopen(url, timeout):
        raise error

    monkeypatch.setattr(capabilities_module, "urlopen", fake_urlopen)

    result = capabilities_module.check_model_availability(settings)

    assert result.status == "service_unavailable"


def test_handles_invalid_json(monkeypatch, settings):
    def fake_urlopen(url, timeout):
        return io.BytesIO(b"This is not JSON")

    monkeypatch.setattr(capabilities_module, "urlopen", fake_urlopen)

    result = capabilities_module.check_model_availability(settings)

    assert result.status == "invalid_response"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"models": "unexpected value"},
    ],
)
def test_handles_unexpected_response_shape(monkeypatch, settings, payload):
    mock_response(monkeypatch, payload)

    result = capabilities_module.check_model_availability(settings)

    assert result.status == "invalid_response"


def test_uses_configured_address_and_timeout(monkeypatch, settings):
    calls = []

    def fake_urlopen(url, timeout):
        calls.append((url, timeout))
        return io.BytesIO(b'{"models": []}')

    monkeypatch.setattr(capabilities_module, "urlopen", fake_urlopen)
    custom_settings = settings.model_copy(
        update={"ollama_base_url": "http://localhost:11434/"}
    )

    capabilities_module.check_model_availability(
        custom_settings,
        timeout_seconds=2,
    )

    assert calls == [("http://localhost:11434/api/tags", 2)]