from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="GT_",
        extra="forbid",
    )

    llm_provider: Literal["ollama"] = "ollama"
    llm_model: str = Field(default="qwen3:4b-instruct", min_length=1)
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_temperature: float = Field(default=0, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_structured_method: Literal[
        "json_schema", "json_mode", "function_calling"
    ] = "json_schema"
    timezone: str = "Asia/Kolkata"

def load_settings(env_file: str | None = None) -> Settings:
    """Load settings from an optional .env file and the environment."""
    return Settings(
        _env_file=env_file,
        _env_file_encoding="utf-8",
    )

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check application settings.")
    parser.add_argument("--env-file")
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    print(settings.model_dump_json(indent=2))