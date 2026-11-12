# Goal Tracker

A standalone terminal application for weekly and monthly goal tracking,
built incrementally with Python, SQLite, LangChain and local Ollama.

OpenCode and VS Code are development tools, not runtime dependencies.

## Development progress

### Phase 1: Model integration foundation

- Environment-based configuration with validation.
- Centralized LangChain model initialization.
- Structured activity extraction and evidence-quote checks.
- Ollama connectivity and installed-model checks.
- Optional live generation and extraction diagnostics.

### Phase 2: Application skeleton

- Platform-appropriate application data paths.
- Configurable data directory and log level.
- Rotating logs with UTC timestamps and request IDs.
- Explicit startup setup.
- Interactive terminal navigation.

### Phase 3: Durable storage

- SQLite connections with foreign-key enforcement.
- Explicit transactions and rollback.
- Initial application schema.
- Numbered migrations with checksums.
- Automatic database initialization during startup.
- Backups before database upgrades.
- Explicit database backup command.

### Phase 4: Goal management

- Weekly and monthly goals with multiple milestones.
- Current and future calendar-period planning.
- Review and confirmation before saving.
- Multiple goals in the same period.
- Goal browsing with lifecycle filters and pagination.
- Title, description, outcome and deadline edits.
- Milestone additions, edits, removal from scope and restoration.
- Cancellation and archiving without deleting history.
- Goal and milestone revisions.
- Request deduplication and conflict detection.
- Atomic audit records.
- Initial-plan finalization and identification of later additions.
- Goal history and paginated audit viewing.

Daily progress recording, completion calculations, MCP integration,
runtime Skills, recommendations and reports remain to be implemented.

## Requirements

- Python 3.12
- uv
- Ollama for AI features
- qwen3:4b-instruct for the initial model configuration

Goal creation and management do not require Ollama to be running.

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

## Start the application

Use explicit configuration:

```powershell
uv run goal-tracker start --env-file .env
```

Or use defaults and environment variables:

```powershell
uv run goal-tracker
```

Equivalent Python module entry point:

```powershell
uv run python -m goal_tracker start --env-file .env
```

Startup configures logging and initializes or upgrades SQLite.

## Terminal workflows

Available menu options:

- 1: Create Weekly Goals.
- 2: Create Monthly Goals.
- 4: View / Manage Goals.
- 9: Diagnostics.
- 0: Exit.

Other options are labelled Planned.

### Create goals

1. Choose a calendar period.
2. Enter a title, optional description and expected outcome.
3. Set an optional goal deadline.
4. Add at least one milestone.
5. Review the preview and confirm the save.
6. Optionally add more goals to the same period.
7. Optionally finalize the initial plan.

Weeks run Monday through Sunday.
Months run from the first through the last calendar day.

An unspecified milestone deadline uses the goal deadline,
or the period end if no explicit goal deadline exists.

### Original and revised plans

Each goal's creation revision and initial milestones remain its baseline.

Finalizing a period's initial plan identifies the original set of goals.
Goals created afterward are labelled additions.

New milestones added during editing are outside the goal's creation baseline.
Removed milestones remain in history.

Finalization does not prevent later goals or edits.
Reopening a finalized initial plan is not supported.

### Manage goals

Choose option 4, select a lifecycle filter and choose a goal number.

Available actions depend on the goal's status:

- Edit active goals.
- View details and history.
- Cancel active goals.
- Archive active or cancelled goals.
- Finalize an open initial plan before the period ends.

Use n and p where offered to navigate pages.
Use f to change the goal-list filter.
Use 0 to return to the previous screen.

### Editing rules

- Every edit requires a reason and confirmation.
- The expected revision prevents stale edits from overwriting newer changes.
- Goal edits append revisions rather than replacing historical descriptions.
- Included milestones must have unique descriptions.
- An active goal must retain at least one included milestone.
- Milestone deadlines cannot exceed the goal deadline.
- Ended-period corrections require an explicit effective date.
- Effective dates cannot precede the latest goal revision.
- Cancelled and archived goals cannot be edited.
- Cancellation and archiving preserve the goal and its history.

If a storage failure offers a retry, it reuses the same request ID.
A confirmed request cannot be reused with different input.

## Configuration

Priority:

1. Environment variables.
2. An explicitly selected .env file.
3. Application defaults.

The application does not automatically discover .env files.

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

Optional settings:

- GT_LOG_LEVEL: DEBUG, INFO, WARNING, ERROR or CRITICAL.
- GT_DATA_DIR: an absolute application data-directory override.

Keep local settings in .env and keep .env.example free of secrets.

## Application data

The default Windows location is normally:

```text
%LOCALAPPDATA%\GoalTracker
```

Layout:

```text
GoalTracker/
├── tracker.sqlite3
├── logs/
├── backups/
└── reports/
```

SQLite stores goals, revisions, request responses and audit history.
View saved goals through menu option 4.

Startup creates the database and logs.
Backups are created when requested or before schema upgrades.
Report generation is not implemented yet.

The active log rotates at approximately 1 MB with three backups.
Logs use UTC timestamps independently of goal-period timezones.

Log operational events only, not journal text, prompts or model responses.

## Database migrations

Migration SQL lives under src/goal_tracker/db/migrations.

Files use consecutive names such as:

```text
001_initial.sql
002_example_change.sql
```

Applied filenames and checksums are recorded in schema_migrations.

Do not edit, rename or remove an applied migration.
Create a new migration for subsequent schema changes.

Pending migrations and their history records run in one transaction.
Failure rolls back all pending changes.
SQL scripts cannot manage the runner's transaction.

Existing databases are backed up before upgrades.
New databases and unchanged schemas do not need migration backups.

## Backups

Create a backup:

```powershell
uv run goal-tracker db backup --env-file .env
```

The command uses SQLite's backup API and a temporary output file.
Successful backups receive unique UTC timestamp and UUID filenames.

It does not apply migrations or create a missing source database.
Automatic restoration is not implemented.

## Diagnostics

Check configuration and model availability:

```powershell
uv run goal-tracker doctor --env-file .env
```

Also test text generation and sample extraction:

```powershell
uv run goal-tracker doctor --env-file .env --llm
```

View help:

```powershell
uv run goal-tracker --help
```

Doctor does not initialize or migrate the database.
Live checks do not save goals or progress.

## Tests

Run the offline suite:

```powershell
uv run pytest tests/unit -q
```

Tests cover configuration, models, repositories, services,
transactions, migrations, backups, navigation and confirmed workflows.

Database tests use real temporary SQLite files.
Model calls and HTTP responses are simulated where appropriate.
Ollama does not need to be running.

## Model switching

Change GT_LLM_MODEL to another installed Ollama model and rerun
doctor with --llm.

Configuration-only switching does not guarantee model compatibility.
Each replacement model requires structured-output and semantic testing.

Only the Ollama provider is currently supported.

## Architecture

Source code lives under src/goal_tracker.

- config: validated settings and application paths.
- domain: shared enums, input models and application errors.
- db: connections, transactions, migrations and backups.
- db/repositories: SQLite queries.
- goals: goal business rules and operation coordination.
- llm: model factory, extraction schemas, gateway and availability checks.
- cli: commands, navigation, prompts, previews and goal workflows.
- bootstrap.py: application startup.
- logging_config.py: application logging.
- __main__.py: Python module entry point.

Typical goal flow:

```text
Terminal input
    → Pydantic input validation
    → Review and confirmation
    → GoalService
    → Business rules and repositories
    → SQLite transaction, request result and audit history
```

LangChain communicates with Ollama through its local HTTP API.
Ollama manages and runs downloaded model files.

## Reporting rules for future implementation

- Quick progress summaries may be viewed during an active period.
- Comprehensive weekly reports wait until Sunday has ended.
- Comprehensive monthly reports wait until the month has ended.
- Eligibility uses the configured timezone.
- Reporting services must enforce timing rules.
- No background scheduler is planned for the first version.

## Remaining work and limitations

- Daily progress recording and corrections.
- Milestone completion events and deterministic percentages.
- Database retrieval for natural-language progress interpretation.
- MCP server and client integration.
- Runtime Agent Skills.
- Evidence-linked recommendations.
- Saved report snapshots and PDF export.
- Full terminal integration and packaging verification.

The extraction prototype currently handles activities, statuses and quotes.
Quote matching does not prove semantic correctness.
Its output is not yet connected to progress persistence.

Live diagnostics test one sample, not full model compatibility.
Automatic extraction repair retries are not implemented.
Configuration-level URL and timezone validation need strengthening.
Simultaneous migrations from multiple application instances are unsupported.

## Development checkpoints

At each phase boundary:

1. Run automated tests and demonstrate the working features.
2. Update this README.
3. Review changes and confirm local data is excluded.
4. Commit the phase and push it to GitHub.