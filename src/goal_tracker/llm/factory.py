from langchain_ollama import ChatOllama

from goal_tracker.config.settings import Settings


def create_model(settings: Settings) -> ChatOllama:
    """Create the application's configured local chat model."""
    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        client_kwargs={"timeout": settings.llm_timeout_seconds},
    )