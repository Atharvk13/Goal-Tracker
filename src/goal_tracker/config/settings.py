from pathlib import Path
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
    # Tokens of context to request from Ollama. Without it Ollama may use the
    # model's maximum (262k for qwen3), which needs ~18 GB of memory.
    llm_context_length: int = Field(default=8192, ge=2048, le=262144)
    llm_structured_method: Literal[
        "json_schema", "json_mode", "function_calling"
    ] = "json_schema"

    timezone: str = "Asia/Kolkata"
    data_dir: Path | None = None
    log_level: Literal[
        "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
    ] = "INFO"

    # Optional path to your own daily mail template; the bundled
    # mail/daily_template.txt is used when this is not set.
    daily_mail_template: Path | None = None

    # Recipients for the daily mail, separated by ";" or ",".
    mail_to: str = ""
    mail_cc: str = ""
    # "open"  opens a filled message in your default mail app (works with the
    #         new Outlook); you press Send there.
    # "send"  sends directly through classic Outlook after you confirm.
    # "draft" opens the filled message in classic Outlook for you to send.
    mail_send_mode: Literal["open", "send", "draft"] = "open"

    # Jarvis prepares follow-ups (weekdays 18:00), a Friday wrap-up (17:00)
    # and Monday planning (09:00) while the web app is open. Every change is
    # a suggestion you confirm.
    agent_schedule: bool = True


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