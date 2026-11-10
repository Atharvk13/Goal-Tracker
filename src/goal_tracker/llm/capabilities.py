import json
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pydantic import BaseModel

from goal_tracker.config.settings import Settings


class AvailabilityResult(BaseModel):
    """Result of checking Ollama and the configured model."""

    status: Literal[
        "available",
        "service_unavailable",
        "model_missing",
        "invalid_response",
    ]
    message: str
    model: str
    digest: str | None = None


def check_model_availability(
    settings: Settings,
    timeout_seconds: float = 5,
) -> AvailabilityResult:
    """Check model installation without generating a response."""
    url = settings.ollama_base_url.rstrip("/") + "/api/tags"

    try:
        with urlopen(url, timeout=timeout_seconds) as response:
            data = json.load(response)

    except HTTPError:
        return AvailabilityResult(
            status="service_unavailable",
            message="Ollama returned an HTTP error. Check the service address.",
            model=settings.llm_model,
        )

    except (URLError, TimeoutError, OSError):
        return AvailabilityResult(
            status="service_unavailable",
            message="Cannot reach Ollama. Check that it is running.",
            model=settings.llm_model,
        )

    except (ValueError, UnicodeError):
        return AvailabilityResult(
            status="invalid_response",
            message="The configured address did not return valid Ollama JSON.",
            model=settings.llm_model,
        )

    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return AvailabilityResult(
            status="invalid_response",
            message="Ollama returned an unexpected model-list format.",
            model=settings.llm_model,
        )

    for model in data["models"]:
        if not isinstance(model, dict):
            continue

        if model.get("name") == settings.llm_model:
            digest = model.get("digest")

            return AvailabilityResult(
                status="available",
                message="Ollama is reachable and the configured model is installed.",
                model=settings.llm_model,
                digest=digest if isinstance(digest, str) else None,
            )

    return AvailabilityResult(
        status="model_missing",
        message="Ollama is reachable, but the configured model is not installed.",
        model=settings.llm_model,
    )