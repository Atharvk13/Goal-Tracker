from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from goal_tracker.domain.progress import (
    ActivityInput,
    ActivityKind,
    ActivityState,
    DailyProgressInput,
    EntrySource,
    MilestoneProgress,
)
from goal_tracker.bootstrap import AppContext
from goal_tracker.cli.goal_views import show_goal_list
from goal_tracker.cli.progress_views import (
    show_entry_list,
    show_pending_activities,
    show_progress_draft,
    show_progress_entry,
)
from goal_tracker.cli.prompts import (
    ask_date,
    ask_required_text,
    confirm_and_execute,
    show_application_error,
    show_validation_error,
)
from goal_tracker.domain.errors import AppError
from goal_tracker.domain.progress import (
    ActivityInput,
    ActivityKind,
    ActivityState,
    DailyProgressInput,
    MilestoneProgress,
)
from goal_tracker.goals.service import GoalService
from goal_tracker.progress.service import ProgressService
from goal_tracker.llm.gateway import ModelGateway

PAGE_SIZE = 20


def _today(context: AppContext) -> date:
    return datetime.now(ZoneInfo(context.settings.timezone)).date()


def _ask_activity_date(context: AppContext, console: Console) -> date:
    today = _today(context)

    while True:
        selected = ask_date(
            "Activity date (YYYY-MM-DD)",
            console,
            default=today,
        )

        if selected is None:
            continue

        if selected > today:
            console.print("Choose today or an earlier date.", style="yellow")
            continue

        return selected


def _ask_journal(console: Console) -> str:
    while True:
        console.print(
            "Enter your journal text. Finish with a single . on its own line."
        )
        lines = []

        while True:
            line = console.input("> ")

            if line == ".":
                break

            lines.append(line)

        text = "\n".join(lines)

        if text.strip():
            return text

        console.print("Please enter some journal text.", style="yellow")


def _ask_minutes(console: Console) -> int | None:
    while True:
        value = Prompt.ask(
            "Minutes spent (Enter if not provided)",
            console=console,
        ).strip()

        if not value:
            return None

        try:
            minutes = int(value)

            if minutes < 0:
                raise ValueError

            return minutes

        except ValueError:
            console.print(
                "Enter a nonnegative whole number or leave it blank.",
                style="yellow",
            )


def _select_numbers(
    label: str,
    count: int,
    console: Console,
) -> list[int]:
    """Return zero-based selections; blank input means no selection."""
    while True:
        value = Prompt.ask(label, console=console).strip()

        if not value:
            return []

        try:
            numbers = [int(part.strip()) for part in value.split(",")]

            if (
                len(numbers) != len(set(numbers))
                or any(number < 1 or number > count for number in numbers)
            ):
                raise ValueError

            return [number - 1 for number in numbers]

        except ValueError:
            console.print(
                "Enter unique numbers from the displayed list, "
                "separated by commas.",
                style="yellow",
            )


def _select_goals(goals: list[dict], console: Console) -> list[dict]:
    if not goals:
        console.print("No goals are available on this date. Record unlinked work.")
        return []

    show_goal_list(goals, console)

    selected = _select_numbers(
        "Goal numbers, separated by commas (Enter for unlinked)",
        len(goals),
        console,
    )

    return [goals[index] for index in selected]


def _select_milestone_changes(
    goals: list[dict],
    state: ActivityState,
    used_ids: set[str],
    console: Console,
) -> list[MilestoneProgress]:
    candidates = [
        (goal, milestone)
        for goal in goals
        for milestone in goal["milestones"]
        if milestone["is_included"] and milestone["id"] not in used_ids
    ]

    if not candidates:
        return []

    if not Confirm.ask(
        "Record an explicit milestone state change?",
        default=False,
        console=console,
    ):
        return []

    table = Table(title="Milestone Choices")
    table.add_column("#")
    table.add_column("Goal")
    table.add_column("Milestone")

    for number, (goal, milestone) in enumerate(candidates, start=1):
        table.add_row(
            str(number),
            Text(goal["title"]),
            Text(milestone["description"]),
        )

    console.print(table)

    selected = _select_numbers(
        "Milestone numbers (Enter to skip)",
        len(candidates),
        console,
    )

    if not selected:
        return []

    if state == ActivityState.COMPLETED:
        milestone_state = Prompt.ask(
            "New milestone state",
            choices=["done", "not_done"],
            default="done",
            console=console,
        )
    else:
        milestone_state = "not_done"
        console.print("Selected milestones will be recorded as not done.")

    changes = []

    for index in selected:
        milestone = candidates[index][1]
        used_ids.add(milestone["id"])

        changes.append(
            MilestoneProgress(
                milestone_id=milestone["id"],
                state=milestone_state,
            )
        )

    return changes


def _ask_item(
    goals: list[dict],
    used_milestones: set[str],
    console: Console,
) -> ActivityInput:
    kind = ActivityKind(
        Prompt.ask(
            "Item kind",
            choices=[kind.value for kind in ActivityKind],
            default="activity",
            console=console,
        )
    )

    description = ask_required_text("Item description", console)

    if kind == ActivityKind.ACTIVITY:
        state = ActivityState(
            Prompt.ask(
                "Activity state",
                choices=["completed", "pending", "in_progress"],
                default="in_progress",
                console=console,
            )
        )
    else:
        state = ActivityState.NOT_APPLICABLE

    minutes = _ask_minutes(console)
    selected_goals = _select_goals(goals, console)

    milestone_changes = (
        _select_milestone_changes(
            selected_goals,
            state,
            used_milestones,
            console,
        )
        if kind == ActivityKind.ACTIVITY
        else []
    )

    return ActivityInput(
        kind=kind,
        description=description,
        state=state,
        minutes=minutes,
        goal_ids=[goal["id"] for goal in selected_goals],
        milestone_changes=milestone_changes,
    )


def _save_draft(
    service: ProgressService,
    draft: DailyProgressInput,
    goals: dict[str, dict],
    console: Console,
) -> None:
    duplicates = service.find_duplicates(
        draft.activity_date,
        draft.raw_text,
    )

    if duplicates:
        console.print(
            "\nMatching entries already exist on this date:",
            style="yellow",
        )

        for existing in duplicates:
            console.print(
                f"Recorded at: {existing['created_at']}",
                markup=False,
            )
            console.print(Text(existing["raw_text"]))

        if not Confirm.ask(
            "Record another entry with this text?",
            default=False,
            console=console,
        ):
            console.print("Cancelled. No additional entry was requested.")
            return

        draft = DailyProgressInput.model_validate(
            {
                **draft.model_dump(),
                "allow_duplicate": True,
            }
        )

    show_progress_draft(draft, goals, console)

    result = confirm_and_execute(
        lambda: service.record_progress(draft),
        console,
        confirmation="Save this progress entry?",
    )

    if result is not None:
        console.print("Progress saved.", style="green")
        show_progress_entry(result["entry"], goals, console)

def _review_ai_activities(
    context: AppContext,
    raw_text: str,
    goals: list[dict],
    console: Console,
) -> list[ActivityInput] | None:
    """Extract activity proposals and review them before saving."""
    try:
        with console.status("Interpreting your update with the local model..."):
            proposal = ModelGateway(
                context.settings
            ).extract_progress(raw_text)

    except Exception as error:
        context.logger.warning(
            "Progress extraction failed (%s).",
            type(error).__name__,
        )
        console.print(
            "AI interpretation is unavailable. You can use manual entry.",
            style="yellow",
        )
        return None

    if not proposal.activities:
        console.print(
            "No activities with an explicit status were found. "
            "You can use manual entry.",
            style="yellow",
        )
        return None

    activities = []
    used_milestones: set[str] = set()

    for number, item in enumerate(proposal.activities, start=1):
        console.print(f"\nActivity {number}", style="bold")
        console.print(f"Description: {item.description}", markup=False)
        console.print(f"Status: {item.status}", markup=False)
        console.print(
            f"Supporting quote: {item.evidence_quote}",
            markup=False,
        )

        if not Confirm.ask(
            "Include this activity?",
            default=True,
            console=console,
        ):
            continue

        while True:
            description = Prompt.ask(
                "Description",
                default=item.description,
                console=console,
            ).strip()

            if description:
                break

            console.print("The description cannot be blank.")

        state = ActivityState(
            Prompt.ask(
                "Status",
                choices=["completed", "pending", "in_progress"],
                default=item.status,
                console=console,
            )
        )

        selected_goals = _select_goals(goals, console)
        minutes = _ask_minutes(console)

        milestone_changes = _select_milestone_changes(
            selected_goals,
            state,
            used_milestones,
            console,
        )

        used_milestones.update(
            str(change.milestone_id)
            for change in milestone_changes
        )

        activities.append(
            ActivityInput(
                kind=ActivityKind.ACTIVITY,
                description=description,
                state=state,
                minutes=minutes,
                goal_ids=[
                    goal["id"] for goal in selected_goals
                ],
                evidence_quote=item.evidence_quote,
                milestone_changes=milestone_changes,
            )
        )

    return activities

def _record_entry(
    context: AppContext,
    service: ProgressService,
    console: Console,
) -> None:
    activity_date = _ask_activity_date(context, console)
    raw_text = _ask_journal(console)
    goals = service.list_recordable_goals(activity_date)
    goal_lookup = {goal["id"]: goal for goal in goals}

    method = Prompt.ask(
        "Recording method",
        choices=["manual", "ai"],
        default="manual",
        console=console,
    )

    activities = []
    source = EntrySource("manual")

    if method == "ai":
        reviewed = _review_ai_activities(
            context,
            raw_text,
            goals,
            console,
        )

        if reviewed is None:
            if not Confirm.ask(
                "Continue with manual entry?",
                default=True,
                console=console,
            ):
                console.print("Entry cancelled. Nothing was saved.")
                return

        elif not reviewed:
            console.print(
                "No activities were accepted. Nothing was saved."
            )
            return

        else:
            activities = reviewed
            source = EntrySource("llm_reviewed")

    if not activities:
        used_milestones: set[str] = set()

        while True:
            activities.append(
                _ask_item(goals, used_milestones, console)
            )

            if not Confirm.ask(
                "Add another item to this entry?",
                default=False,
                console=console,
            ):
                break

    draft = DailyProgressInput(
        activity_date=activity_date,
        raw_text=raw_text,
        activities=activities,
        source=source,
    )

    _save_draft(service, draft, goal_lookup, console)


def _load_pending(
    service: ProgressService,
    as_of: date,
) -> list[dict]:
    activities = []
    offset = 0

    while True:
        page = service.list_pending_activities(
            as_of=as_of,
            limit=100,
            offset=offset,
        )
        activities.extend(page)

        if len(page) < 100:
            return activities

        offset += 100


def _update_pending(
    context: AppContext,
    service: ProgressService,
    console: Console,
) -> None:
    activity_date = _ask_activity_date(context, console)
    goals = service.list_recordable_goals(activity_date)
    goal_lookup = {goal["id"]: goal for goal in goals}

    pending = [
        activity
        for activity in _load_pending(service, activity_date)
        if all(
            goal_id in goal_lookup
            for goal_id in activity["goal_ids"]
        )
    ]

    if not pending:
        console.print("No pending work can be updated on this date.")
        return

    show_pending_activities(pending, goal_lookup, console)

    choice = Prompt.ask(
        "Activity number (0 to go back)",
        choices=[
            *[str(number) for number in range(1, len(pending) + 1)],
            "0",
        ],
        console=console,
    )

    if choice == "0":
        return

    previous = pending[int(choice) - 1]

    state = ActivityState(
        Prompt.ask(
            "New activity state",
            choices=["completed", "pending", "in_progress"],
            default="completed",
            console=console,
        )
    )
    description = ask_required_text(
        "Update description",
        console,
        default=previous["description"],
    )
    minutes = _ask_minutes(console)
    raw_text = _ask_journal(console)

    selected_goals = [
        goal_lookup[goal_id]
        for goal_id in previous["goal_ids"]
    ]

    changes = _select_milestone_changes(
        selected_goals,
        state,
        set(),
        console,
    )

    draft = DailyProgressInput(
        activity_date=activity_date,
        raw_text=raw_text,
        activities=[
            ActivityInput(
                description=description,
                state=state,
                minutes=minutes,
                goal_ids=previous["goal_ids"],
                updates_activity_id=previous["id"],
                milestone_changes=changes,
            )
        ],
    )

    _save_draft(service, draft, goal_lookup, console)


def _load_goal_lookup(service: GoalService) -> dict[str, dict]:
    goals = {}
    offset = 0

    while True:
        page = service.list_goals(limit=100, offset=offset)
        goals.update({goal["id"]: goal for goal in page})

        if len(page) < 100:
            return goals

        offset += 100


def _view_entries(
    context: AppContext,
    service: ProgressService,
    goal_lookup: dict[str, dict],
    console: Console,
) -> None:
    today = _today(context)

    start_date = ask_date(
        "Start date",
        console,
        default=today - timedelta(days=6),
    )
    end_date = ask_date(
        "End date",
        console,
        default=today,
    )

    if start_date is None or end_date is None:
        return

    if start_date > end_date:
        console.print("Start date cannot follow end date.", style="yellow")
        return

    offset = 0

    while True:
        entries = service.get_daily_progress(
            start_date,
            end_date,
            limit=PAGE_SIZE,
            offset=offset,
        )

        if not entries and offset > 0:
            console.print("No more entries.")
            offset = max(0, offset - PAGE_SIZE)
            continue

        show_entry_list(entries, console)

        choices = [
            str(number)
            for number in range(1, len(entries) + 1)
        ]
        choices.append("0")

        if len(entries) == PAGE_SIZE:
            choices.append("n")

        if offset > 0:
            choices.append("p")

        choice = Prompt.ask(
            "Entry number, n = next, p = previous, 0 = back",
            choices=choices,
            console=console,
        )

        if choice == "0":
            return

        if choice == "n":
            offset += PAGE_SIZE
        elif choice == "p":
            offset -= PAGE_SIZE
        else:
            show_progress_entry(
                entries[int(choice) - 1],
                goal_lookup,
                console,
            )


def run_progress_menu(
    context: AppContext,
    console: Console,
) -> None:
    """Record and inspect daily progress without requiring Ollama."""
    try:
        service = ProgressService(
            context.paths.database,
            timezone=context.settings.timezone,
        )
        goal_service = GoalService(
            context.paths.database,
            timezone=context.settings.timezone,
        )
        goal_lookup = _load_goal_lookup(goal_service)

    except AppError as error:
        show_application_error(error, console)
        return

    while True:
        console.print(
            "\nDaily Progress\n"
            "1. Record a new entry\n"
            "2. Update pending work\n"
            "3. View saved entries\n"
            "4. View pending work\n"
            "0. Back"
        )

        choice = Prompt.ask(
            "Choose an action",
            choices=["1", "2", "3", "4", "0"],
            console=console,
        )

        if choice == "0":
            return

        try:
            if choice == "1":
                _record_entry(context, service, console)

            elif choice == "2":
                _update_pending(context, service, console)

            elif choice == "3":
                _view_entries(context, service, goal_lookup, console)

            elif choice == "4":
                as_of = _ask_activity_date(context, console)
                show_pending_activities(
                    _load_pending(service, as_of),
                    goal_lookup,
                    console,
                )

        except ValidationError as error:
            show_validation_error(error, console)

        except AppError as error:
            show_application_error(error, console)