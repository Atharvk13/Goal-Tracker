# Goal Tracker

A standalone, terminal-based goal tracker using Python, LangChain
and local Ollama models.

OpenCode and VS Code are development tools, not runtime dependencies.

## Development progress

### Phase 1: Model integration foundation

- Isolated Python environment managed by uv.
- Validated configuration with Pydantic settings.
- Centralized LangChain model factory.
- Structured activity extraction with evidence-quote validation.
- Ollama availability and installed-model checks.
- Diagnostic commands with optional live model checks.
- Offline automated tests.

### Phase 2: Application skeleton

- Platform-appropriate application data paths.
- Configurable data directory and log level.
- Rotating application logs with UTC timestamps.
- Request ID support in log records.
- Explicit startup setup through bootstrap.
- Interactive terminal menu.
- Diagnostics available from the menu.
- Graceful exit on Ctrl+C or closed input.
- Tests for paths, logging, startup and navigation.

Goal management, SQLite persistence, MCP, runtime Skills, progress
calculations and PDF reports are not implemented yet.

## Requirements

- Python 3.12
- uv
- Ollama for AI features
- qwen3:4b-instruct for the initial model configuration

## Setup

Run commands from the project folder.

Install the application and locked dependencies:

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

## Start the application

Open the interactive menu with explicit configuration:

```powershell
uv run goal-tracker start --env-file .env
```

Or start using defaults and environment variables:

```powershell
uv run goal-tracker
```

Equivalent Python module entry point:

```powershell
uv run python -m goal_tracker start --env-file .env
```

The menu currently supports diagnostics and exit.
Unimplemented features are labelled Planned.

Opening the menu does not require Ollama.
Selecting Diagnostics checks Ollama connectivity and model installation.

## Configuration

Settings are loaded in this order of priority:

1. Environment variables.
2. An explicitly selected .env file.
3. Application defaults.

The application does not automatically discover .env files.

Initial configuration:

```dotenv
GT_LLM_PROVIDER=ollama
GT_LLM_MODEL=qwen3:4b-instruct
GT_OLLAMA_BASE_URL=http://127.0.0.1:11434
GT_LLM_TEMPERATURE=0
GT_LLM_TIMEOUT_SECONDS=120
GT_LLM_STRUCTURED_METHOD=json_schema
GT_TIMEZONE=Asia/Kolkata
```

Optional logging configuration:

```dotenv
GT_LOG_LEVEL=INFO
```

Supported levels: DEBUG, INFO, WARNING, ERROR and CRITICAL.

GT_DATA_DIR can override the application data directory.
It must be an absolute path. Otherwise, platformdirs selects the default.

Keep personal settings in .env and keep .env.example free of secrets.

## Application data

On Windows, the default base directory is normally:

```text
%LOCALAPPDATA%\GoalTracker
```

Planned layout:

```text
GoalTracker/
├── tracker.sqlite3
├── logs/
├── backups/
└── reports/
```

Startup currently creates the logs directory and opens goal_tracker.log.
The database, backups and report files are not created yet.

Logs use UTC timestamps, independently of the timezone used for goal dates.
The active log rotates at approximately 1 MB, retaining three backups.

Log operational events only. Do not log journal text, full prompts,
model responses or secrets.

## Diagnostics

Check configuration and model availability:

```powershell
uv run goal-tracker doctor --env-file .env
```

Also test generation and sample structured extraction:

```powershell
uv run goal-tracker doctor --env-file .env --llm
```

View command help:

```powershell
uv run goal-tracker --help
```

Live checks do not save progress or modify installed models.
The first request may take longer while Ollama loads the model.

## Tests

Run the offline suite:

```powershell
uv run pytest tests/unit -q
```

Tests use temporary storage and simulated model or HTTP responses.
Ollama does not need to be running.

## Model switching

Change GT_LLM_MODEL to another installed Ollama model and rerun
diagnostics with --llm.

Configuration-only switching does not guarantee compatibility.
Each replacement model needs structured-output and semantic testing.

Only the Ollama provider is currently supported.

## Architecture

Source code lives under src/goal_tracker.

- config/settings.py: settings loading and validation.
- config/paths.py: application storage locations.
- logging_config.py: rotating application logging.
- bootstrap.py: startup setup and shared application context.
- llm/factory.py: centralized model initialization.
- llm/schemas.py: structured-output contracts.
- llm/gateway.py: extraction and evidence validation.
- llm/capabilities.py: service and model availability checks.
- cli/app.py: terminal commands and startup integration.
- cli/menu.py: deterministic numbered navigation.
- __main__.py: Python module entry point.
- tests/unit/: automated offline tests.

LangChain communicates with Ollama through its local HTTP API.
Ollama manages and runs the downloaded model files.

## Reporting rules for future implementation

- Quick progress summaries may be viewed during an active period.
- Comprehensive weekly reports become available after Sunday ends.
- Comprehensive monthly reports become available after the month ends.
- Eligibility uses the configured timezone.
- Reporting services must enforce these rules, not just menu labels.
- No background scheduler is planned for the first version.

## Current limitations

- Menu options for goals, progress and reports are placeholders.
- No user progress is persisted.
- Extraction currently covers activities, statuses and evidence quotes.
- Matching an evidence quote does not prove the interpretation is correct.
- User review will be required before future database writes.
- Live diagnostics test one extraction example, not full compatibility.
- Automatic extraction repair retries are not implemented.
- Service URL and timezone validation still need strengthening.
- Startup failures produce a concise message; diagnostic detail can improve.

## Development checkpoints

At each phase boundary:

1. Run automated tests and demonstrate the working features.
2. Update this README.
3. Review changes and confirm local settings are excluded.
4. Commit the phase and push it to GitHub.