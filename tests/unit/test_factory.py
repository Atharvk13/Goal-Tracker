import pytest

import goal_tracker.llm.factory as factory_module
from goal_tracker.config.settings import Settings


@pytest.mark.parametrize(
    "model_name",
    [
        "qwen3:4b-instruct",
        "another-local-model",
    ],
)
def test_factory_passes_configuration(monkeypatch, model_name):
    captured = {}
    fake_model = object()

    def fake_chat_ollama(**kwargs):
        captured.update(kwargs)
        return fake_model

    monkeypatch.setattr(
        factory_module,
        "ChatOllama",
        fake_chat_ollama,
    )

    settings = Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_model=model_name,
        ollama_base_url="http://127.0.0.1:11434",
        llm_temperature=0.2,
        llm_timeout_seconds=45,
        llm_structured_method="json_schema",
        timezone="Asia/Kolkata",
    )

    model = factory_module.create_model(settings)

    assert model is fake_model
    assert captured == {
        "model": model_name,
        "base_url": "http://127.0.0.1:11434",
        "temperature": 0.2,
        "client_kwargs": {"timeout": 45},
    }