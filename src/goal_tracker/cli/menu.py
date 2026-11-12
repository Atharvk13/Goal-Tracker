from collections.abc import Callable

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from goal_tracker.bootstrap import AppContext


MENU_OPTIONS = {
    "1": "Create Weekly Goals",
    "2": "Create Monthly Goals",
    "3": "Record Daily Progress",
    "4": "View / Manage Goals",
    "5": "View Progress Summary",
    "6": "View Completed Week's Report",
    "7": "View Completed Month's Report",
    "8": "View / Export Previous Reports",
    "9": "Diagnostics",
    "0": "Exit",
}


def run_menu(
    context: AppContext,
    diagnostics: Callable[[], None],
    console: Console | None = None,
) -> None:
    """Display terminal navigation without interpreting choices with an LLM."""
    if console is None:
        console = Console()

    console.print(
        "\nWELCOME TO GOAL TRACKER AGENT",
        style="bold cyan",
    )
    console.print(
        f"Configured model: {context.settings.llm_model}",
        markup=False,
    )

    try:
        while True:
            table = Table(title="Main Menu")
            table.add_column("Option")
            table.add_column("Action")
            table.add_column("Availability")

            for number, label in MENU_OPTIONS.items():
                availability = (
                    "Ready" if number in {"9", "0"} else "Planned"
                )
                table.add_row(number, label, availability)

            console.print(table)

            choice = Prompt.ask(
                "Choose an option",
                choices=list(MENU_OPTIONS),
                console=console,
            )

            if choice == "0":
                context.logger.info("User exited the terminal menu.")
                console.print("Goodbye.")
                return

            if choice == "9":
                context.logger.info("User requested diagnostics.")
                diagnostics()
                continue

            console.print(
                "This feature is planned but not implemented yet.",
                style="yellow",
            )

    except (KeyboardInterrupt, EOFError):
        context.logger.info("Terminal menu interrupted or input closed.")
        console.print("\nGoodbye.")