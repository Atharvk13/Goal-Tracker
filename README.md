# Goal Tracker

A standalone Python terminal application for weekly/monthly goals
and daily progress recording.

It uses SQLite for persistent data and Ollama through LangChain
for local model integration.

OpenCode and VS Code are development tools, not runtime dependencies.

## Working functionality

### Goal management

- Create multiple weekly or monthly goals.
- Define descriptions, expected outcomes, deadlines and milestones.
- Review and confirm goals before saving.
- Browse goals with filters and pagination.
- Edit goals and milestone scope.
- Cancel or archive goals without deleting history.
- Preserve goal and milestone revisions.
- Finalize the initial plan and identify later additions.
- View revision and audit history.

Weeks run Monday through Sunday.
Months use calendar boundaries.

### Daily progress

- Record multiple entries on the same date.
- Record completed, pending and ongoing activities.
- Include learnings, challenges, observations and achievements.
- Provide optional time spent in whole minutes.
- Link one activity to multiple goals.
- Explicitly mark selected milestones done or not done.
- Update previously recorded pending work.
- View saved entries and pending activities.
- Review possible duplicate entries before allowing another.
- Preserve original journal text.
- Retain entries and status history across restarts.

The current form uses manual structured input.
Natural-language extraction exists as a prototype but is not yet connected
to the recording workflow.

### Progress summaries

- View weekly or monthly progress summaries.
- Calculate original-plan and current-plan completion from milestones.
- Show goal-wise completion and daily completion trends.
- Show logging coverage and recorded time.
- Display completed work, pending items, learnings and challenges.
- Calculate factual metrics in Python.

LLM recommendations are not connected yet.

### Storage and diagnostics

- SQLite transactions and foreign-key enforcement.
- Versioned migrations with checksum validation.
- Automatic schema initialization during startup.
- Backups before database upgrades.
- Explicit backup command.
- Request IDs that prevent duplicate saves on retries.
- Audit records for confirmed changes.
- Rotating operational logs.
- Local model availability and generation diagnostics.

## Requirements

- Python 3.12
- uv
- Ollama for model features
- qwen3:4b-instruct for the initial model configuration

Manual goal, progress and summary workflows work without Ollama.

## Setup

From the project directory, install locked dependencies:

```powershell
uv sync --locked
```

If .env does not already exist, create it:

```powershell
Copy-Item -LiteralPath .env.example -Destination .env
```

Download the initial model when needed:

```powershell
ollama pull qwen3:4b-instruct
```

## Run

Start with explicit configuration:

```powershell
uv run goal-tracker start --env-file .env
```

Equivalent Python entry point:

```powershell
uv run python -m goal_tracker start --env-file .env
```

Running without arguments uses defaults and environment variables:

```powershell
uv run goal-tracker
```

The application does not automatically discover .env files.

## Terminal navigation

Available main-menu options:

- 1: Create Weekly Goals.
- 2: Create Monthly Goals.
- 3: Record Daily Progress.
- 4: View / Manage Goals.
- 5: View Progress Summary.
- 6: View Completed Week's Report — planned.
- 7: View Completed Month's Report — planned.
- 8: View / Export Previous Reports — planned.
- 9: Diagnostics.
- 0: Exit.

Choose option 5, select weekly or monthly, then enter the period start
date or press Enter to use the current period.

Quick summaries are available during active periods.
Comprehensive reports will be available only after their period ends.

### Recording progress

Choose main-menu option 3, then:

- 1: Record a new entry.
- 2: Update pending work.
- 3: View saved entries.
- 4: View pending work.
- 0: Back.

Finish journal text with a single period (.) on its own line.

Select goal numbers separated by commas when an activity supports
multiple goals. Leave the selection blank for an unlinked item.

Milestones change only when explicitly selected.
A completed activity does not automatically complete a milestone.

Review the structured preview and confirm before saving.

When updating pending work, minutes describe the additional work
reported in the new entry.

### Goal history

Initial goal and milestone revisions remain preserved.

Finalizing initial planning separates the original goal set from
goals added later. Added milestones are outside the creation baseline.

Goal edits require a reason and an expected revision.
An outdated edit is rejected with a conflict.

Ended-period goal corrections require an explicit effective date.
Replacing an earlier daily entry is not implemented yet.

## Configuration

Priority:

1. Environment variables.
2. An explicitly supplied .env file.
3. Defaults.

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

.env contains local configuration; .env.example documents shared defaults.

## Data location

The normal Windows location is:

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

Goals, entries, activities, state events, request responses and audit
history are stored in SQLite.

Operational logs use UTC timestamps.
Journal text, prompts and model responses should not be logged.

## Backups and migrations

Create a backup:

```powershell
uv run goal-tracker db backup --env-file .env
```

Backups use SQLite's backup API and unique filenames.
The command does not apply migrations.

Migration files are under src/goal_tracker/db/migrations.
Do not modify an applied migration; create the next numbered file.

Startup applies pending migrations transactionally and backs up
existing databases before upgrades.

Automatic restoration is not implemented.

## Model diagnostics

Check availability:

```powershell
uv run goal-tracker doctor --env-file .env
```

Also test generation and sample structured extraction:

```powershell
uv run goal-tracker doctor --env-file .env --llm
```

Switch models through GT_LLM_MODEL and rerun compatibility checks.
Only the Ollama provider is currently implemented.

## Tests

Run the offline suite:

```powershell
uv run pytest tests/unit -q
```

Tests use temporary SQLite databases and simulated model responses.
Ollama does not need to be running.

The daily-progress workflow checks cover:

- Saving and retrieval after restart.
- Pending work becoming completed.
- Explicit milestone events.
- Duplicate review and idempotent retries.
- Atomic rollback on failure.
- One activity supporting multiple goals without duplicating time.

## Architecture

Source code is under src/goal_tracker.

- config: settings and application paths.
- domain: validated inputs, enums and shared errors.
- db: connections, migrations, backups and repositories.
- goals: goal management and revision rules.
- progress: daily recording, retrieval and duplicate detection.
- llm: model initialization, schemas and extraction prototype.
- cli: terminal navigation, forms and displays.
- bootstrap.py: application startup.
- logging_config.py: operational logging.
- analysis: deterministic completion metrics and period summaries.

Recording flow:

```text
Terminal input
    → Validated progress model
    → Review and confirmation
    → ProgressService
    → SQLite entry, activity links and state events
    → Saved response and audit history
```

All save operations use transactions.
Retries reuse request IDs to avoid duplicate writes.

## Remaining functionality

- Natural-language recording connected to review and saving.
- Completed-period report generation and PDF export.
- MCP server/client integration.
- Runtime Agent Skills.
- Evidence-linked recommendations.

Comprehensive weekly reports will become available after Sunday ends.
Monthly reports will become available after the calendar month ends.
Quick progress summaries are available during active periods.

## Deferred improvements

- Daily-entry replacement and advanced corrections.
- Broader model compatibility evaluation.
- Automatic extraction repair retries.
- Stronger configuration validation.
- Additional interface polish and packaging verification.

## Development checkpoints

Build a usable workflow, run meaningful checks, update this README,
then commit and push the checkpoint.