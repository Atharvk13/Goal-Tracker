# Goal Tracker

A standalone, terminal-based goal tracker powered by Python,
LangChain and local Ollama models.

## Current implementation

The initial foundation supports:

- Validated environment-based configuration.
- Centralized LangChain model initialization.
- Structured activity extraction with evidence-quote checks.
- Ollama connectivity and installed-model checks.
- A diagnostic CLI with optional live model checks.
- Automated unit tests that do not require Ollama.

Goal management, SQLite persistence, MCP, runtime Skills, the interactive
menu and PDF reports are planned but not implemented yet.

## Requirements

- Python 3.12
- uv
- Ollama for live AI features
- The qwen3:4b-instruct model for the initial configuration

OpenCode is not required to install or run this application.

## Setup

Open a terminal in the project folder.

Install the locked dependencies and the application:

```powershell
uv sync --locked
```

Create local configuration if .env does not already exist:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

Download the initial model if needed:

```powershell
ollama pull qwen3:4b-instruct
```

Ensure Ollama is running before performing model checks.

## Configuration

The application reads configuration from:

1. Environment variables, with highest priority.
2. An explicitly selected .env file.
3. Defaults defined in settings.py.

The .env file is not automatically discovered. Supply --env-file when
running diagnostics to select it explicitly.

Initial settings:

```dotenv
GT_LLM_PROVIDER=ollama
GT_LLM_MODEL=qwen3:4b-instruct
GT_OLLAMA_BASE_URL=http://127.0.0.1:11434
GT_LLM_TEMPERATURE=0
GT_LLM_TIMEOUT_SECONDS=120
GT_LLM_STRUCTURED_METHOD=json_schema
GT_TIMEZONE=Asia/Kolkata
```

Keep personal configuration in .env. Keep .env.example free of secrets.

## Diagnostics

Check configuration, Ollama connectivity and model installation:

```powershell
uv run goal-tracker doctor --env-file .env
```

Also test text generation and structured extraction:

```powershell
uv run goal-tracker doctor --env-file .env --llm
```

The equivalent Python module command is:

```powershell
uv run python -m goal_tracker doctor --env-file .env
```

View command help:

```powershell
uv run goal-tracker --help
```

Diagnostics do not save progress records or modify installed models.
Live checks may take longer when Ollama first loads the model.

## Tests

Run the offline unit tests:

```powershell
uv run pytest tests/unit -q
```

The tests use simulated model and HTTP responses. Ollama does not need
to be running.

## Model switching

Change GT_LLM_MODEL in .env to another installed Ollama model, then
rerun diagnostics with --llm.

Configuration-only switching does not guarantee compatibility.
Every replacement model needs structured-output and semantic testing.

Only the Ollama provider is currently supported.

## Architecture

- config/settings.py: configuration loading and validation.
- llm/factory.py: centralized model initialization.
- llm/schemas.py: structured-output contracts.
- llm/gateway.py: interpretation and proposal validation.
- llm/capabilities.py: service and model availability checks.
- cli/app.py: diagnostic commands and terminal presentation.
- __main__.py: Python module entry point.
- tests/unit/: offline automated tests.

Application source code lives under src/goal_tracker.

## Current limitations

- Structured extraction currently covers activity descriptions,
  statuses and supporting quotes.
- Exact evidence quotes are checked, but interpretation can still
  be incorrect.
- The live diagnostic checks one sample, not comprehensive model
  compatibility.
- A failed extraction is reported; automatic repair retries are
  not implemented yet.
- Timezone and service URL settings currently have only basic
  configuration validation.
- No progress is persisted yet.

## Independence from development tools

OpenCode and VS Code are development tools, not runtime dependencies.

The application uses its own configuration and Python environment.
LangChain communicates with Ollama through its local HTTP API;
it does not load model files directly.