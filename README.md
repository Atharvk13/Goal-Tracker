# Goal Tracker

A standalone, terminal-based goal tracker using Python, LangChain
and local Ollama models.

OpenCode and VS Code are development tools, not runtime dependencies.

## Development progress

### Phase 1: Model integration foundation

- Validated environment-based configuration.
- Centralized LangChain model factory.
- Structured activity extraction with evidence-quote validation.
- Ollama connectivity and installed-model checks.
- Diagnostic commands with optional live model checks.
- Offline automated tests.

### Phase 2: Application skeleton

- Platform-appropriate application data paths.
- Configurable data directory and log level.
- Rotating logs with UTC timestamps and request ID support.
- Explicit application startup through bootstrap.
- Interactive terminal menu with diagnostics.
- Graceful exit on Ctrl+C or closed input.

### Phase 3: Durable storage infrastructure

- SQLite connections with foreign-key enforcement.
- Explicit transactions with rollback on failure.
- Initial schema for goals, milestones, entries, history and reports.
- Numbered migrations tracked in schema_migrations.
- Checksums that detect changes to applied migrations.
- Atomic application of pending migrations.
- Backups before upgrading existing databases.
- Automatic schema initialization during application startup.
- An explicit database backup command.
- Tests for persistence, migrations, backups and failure handling.

Storage infrastructure is available, but goal and progress management
workflows are not implemented yet.

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

Open the menu with explicit configuration:

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

Startup prepares logging and initializes or upgrades the database.
Opening the menu does not require Ollama.

The menu currently supports diagnostics and exit.
Goal, progress and report options remain labelled Planned.

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

Storage layout:

```text
GoalTracker/
├── tracker.sqlite3
├── logs/
├── backups/
└── reports/
```

Startup creates the database and logs.
The backups directory is created when a backup is needed.
Report generation is not implemented yet.

Logs use UTC timestamps, independently of the timezone used for goal dates.
The active log rotates at approximately 1 MB, retaining three backups.

Log operational events only. Do not log journal text, full prompts,
model responses or secrets.

## Database migrations

Migration files live in:

```text
src/goal_tracker/db/migrations/
```

They use consecutive filenames such as:

```text
001_initial.sql
002_example_change.sql
```

Startup applies pending migrations and records their checksums.
Already-applied versions are not rerun.

Do not edit, rename or remove an applied migration.
Create a new numbered migration for subsequent schema changes.

All pending migrations run in one transaction.
If one fails, their schema changes and migration-history inserts roll back.

Migration SQL must not manage transactions itself.
The runner rejects transaction commands such as COMMIT.

Existing databases are backed up before upgrades.
New databases and unchanged schemas do not need migration backups.

## Database backups

Create a backup explicitly:

```powershell
uv run goal-tracker db backup --env-file .env
```

The command:

- Uses SQLite's backup API.
- Reads the existing source database without applying migrations.
- Uses a temporary output file before publishing the completed backup.
- Generates a unique filename using a UTC timestamp and UUID.
- Prints the resulting backup location.
- Reports an error if the source database does not exist.

Automatic restoration is not implemented.

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

Doctor does not initialize or migrate the database.
Live checks do not save progress or modify installed models.

The first model request may take longer while Ollama loads it.

## Tests

Run the offline suite:

```powershell
uv run pytest tests/unit -q
```

Tests use temporary storage and simulated model or HTTP responses.
Database tests use real SQLite files.
Ollama does not need to be running.

Coverage includes configuration, extraction validation, diagnostics,
paths, logging, startup, navigation, transactions, migrations and backups.

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
- bootstrap.py: configuration, logging and database startup.
- db/connection.py: SQLite connections and explicit transactions.
- db/backup.py: SQLite backups.
- db/migrate.py: migration discovery, validation and execution.
- db/migrations/: versioned schema changes.
- llm/factory.py: centralized model initialization.
- llm/schemas.py: structured-output contracts.
- llm/gateway.py: extraction and evidence validation.
- llm/capabilities.py: service and model availability checks.
- cli/app.py: terminal commands and startup integration.
- cli/menu.py: deterministic numbered navigation.
- cli/database.py: database maintenance commands.
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

## Remaining work

- Goal management and milestone workflows.
- Reviewed daily progress recording and corrections.
- Deterministic completion calculations and retrieval.
- MCP server and client integration.
- Runtime Agent Skills.
- Evidence-linked recommendations.
- Saved reports and PDF exports.
- Full terminal workflow integration.

## Current limitations

- Goal and progress input through the menu is not available yet.
- Extraction currently covers activities, statuses and evidence quotes.
- Matching an evidence quote does not prove interpretation is correct.
- User review will be required before future progress writes.
- Live diagnostics test one sample, not full model compatibility.
- Automatic extraction repair retries are not implemented.
- Service URL and timezone validation still need strengthening.
- Calendar boundaries and reporting eligibility need service-level validation.
- Historical immutability rules need enforcement in application services.
- Concurrent migration by multiple application instances is not a supported
  deployment workflow.

## Development checkpoints

At each phase boundary:

1. Run automated tests and demonstrate the working features.
2. Update this README.
3. Review changes and confirm local data is excluded.
4. Commit the phase and push it to GitHub.