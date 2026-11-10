import os

import pytest
from pydantic import ValidationError

from goal_tracker.config.settings import load_settings


@pytest.fixture(autouse=True)
def clear_application_environment(monkeypatch):
    """Keep tests independent of existing GT_ environment variables."""
    for name in list(os.environ):
        if name.upper().startswith("GT_"):
            monkeypatch.delenv(name)


def test_defaults():
    settings = load_settings()

    assert settings.llm_provider == "ollama"
    assert settings.llm_model == "qwen3:4b-instruct"
    assert settings.llm_timeout_seconds == 120


def test_loads_explicit_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GT_LLM_MODEL=another-local-model\n"
        "GT_LLM_TIMEOUT_SECONDS=30\n",
        encoding="utf-8",
    )

    settings = load_settings(str(env_file))

    assert settings.llm_model == "another-local-model"
    assert settings.llm_timeout_seconds == 30


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GT_LLM_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GT_LLM_MODEL", "environment-model")

    settings = load_settings(str(env_file))

    assert settings.llm_model == "environment-model"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GT_LLM_TIMEOUT_SECONDS", "-1"),
        ("GT_LLM_TEMPERATURE", "3"),
        ("GT_LLM_PROVIDER", "unsupported"),
        ("GT_LLM_STRUCTURED_METHOD", "unsupported"),
        ("GT_LLM_MODEL", ""),
    ],
)
def test_rejects_invalid_settings(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        load_settings()


def test_rejects_unknown_env_file_setting(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GT_LLM_MODLE=misspelled-setting\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_settings(str(env_file))