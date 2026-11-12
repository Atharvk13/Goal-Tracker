from collections.abc import Callable
from datetime import date

from pydantic import ValidationError
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.text import Text

from goal_tracker.domain.errors import AppError, StorageError


def ask_required_text(
    label: str,
    console: Console,
    *,
    default: str | None = None,
) -> str:
    """Ask again until a nonblank value is supplied."""
    while True:
        value = Prompt.ask(
            label,
            default=default if default is not None else "",
            show_default=default is not None,
            console=console,
        ).strip()

        if value:
            return value

        console.print("Please enter a nonblank value.", style="yellow")


def ask_date(
    label: str,
    console: Console,
    *,
    default: date | None = None,
    optional: bool = False,
) -> date | None:
    """Read a calendar date using the YYYY-MM-DD format."""
    while True:
        value = Prompt.ask(
            label,
            default=default.isoformat() if default is not None else "",
            show_default=default is not None,
            console=console,
        ).strip()

        if not value:
            if optional:
                return None

            console.print("A date is required.", style="yellow")
            continue

        try:
            parsed = date.fromisoformat(value)

            if parsed.isoformat() != value:
                raise ValueError("Use the YYYY-MM-DD format.")

            return parsed

        except ValueError:
            console.print(
                "Enter a valid date in YYYY-MM-DD format.",
                style="yellow",
            )


def show_validation_error(
    error: ValidationError,
    console: Console,
) -> None:
    """Display validation messages without dumping the input object."""
    details = error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )

    for detail in details:
        location = ".".join(str(part) for part in detail["loc"])
        message = f"{location or 'Input'}: {detail['msg']}"
        console.print(Text(message, style="red"))


def show_application_error(
    error: AppError,
    console: Console,
) -> None:
    """Display a readable application error."""
    console.print(
        Text(f"{error.code}: {error.message}", style="red")
    )


def confirm_and_execute(
    action: Callable[[], dict],
    console: Console,
    *,
    confirmation: str = "Save these changes?",
) -> dict | None:
    """Run an operation only after confirmation, preserving it on retries."""
    if not Confirm.ask(
        confirmation,
        default=False,
        console=console,
    ):
        console.print("Cancelled. No save was requested.")
        return None

    while True:
        try:
            return action()

        except StorageError as error:
            show_application_error(error, console)

            retry = Confirm.ask(
                "Retry the same request?",
                default=False,
                console=console,
            )

            if not retry:
                return None

        except AppError as error:
            show_application_error(error, console)
            return None