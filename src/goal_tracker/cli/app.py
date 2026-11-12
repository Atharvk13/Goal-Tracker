from pathlib import Path
from time import perf_counter
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from goal_tracker.bootstrap import bootstrap
from goal_tracker.cli.menu import run_menu
from goal_tracker.config.settings import load_settings
from goal_tracker.llm.capabilities import check_model_availability
from goal_tracker.llm.factory import create_model
from goal_tracker.llm.gateway import ModelGateway


app = typer.Typer(no_args_is_help=False)
console = Console()


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Goal Tracker: local goal planning and progress analysis."""
    if ctx.invoked_subcommand is None:
        start(env_file=None)


@app.command()
def start(
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Explicit path to the application's .env file.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
):
    """Start the interactive terminal menu."""
    try:
        context = bootstrap(
            str(env_file) if env_file is not None else None
        )
    except (ValidationError, OSError, ValueError) as error:
        console.print(
            f"Startup failed ({type(error).__name__}). "
            "Check settings, directory paths and write permissions."
        )
        raise typer.Exit(code=1)

    def show_diagnostics():
        try:
            doctor(env_file=env_file, llm=False)
        except typer.Exit as error:
            if error.exit_code != 0:
                context.logger.warning("Menu diagnostics reported a failure.")

    run_menu(
        context=context,
        diagnostics=show_diagnostics,
        console=console,
    )


@app.command()
def doctor(
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Explicit path to the application's .env file.",
            exists=True,
            dir_okay=False,
        ),
    ] = None,
    llm: Annotated[
        bool,
        typer.Option(
            "--llm",
            help="Also test generation and structured extraction.",
        ),
    ] = False,
):
    """Check configuration, Ollama and optional model capabilities."""
    table = Table(title="Goal Tracker Diagnostics")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Details")

    try:
        settings = load_settings(
            str(env_file) if env_file is not None else None
        )
    except (ValidationError, OSError):
        table.add_row(
            "Configuration",
            "FAIL",
            "Check setting names, values and configuration file access.",
        )
        console.print(table)
        raise typer.Exit(code=1)

    table.add_row("Configuration", "PASS", "Settings validated.")

    availability = check_model_availability(settings)

    if availability.status != "available":
        table.add_row(
            "Model availability",
            "FAIL",
            availability.message,
        )
        console.print(table)
        raise typer.Exit(code=1)

    table.add_row("Model availability", "PASS", availability.model)

    if availability.digest:
        table.add_row("Model digest", "INFO", availability.digest)

    if not llm:
        table.add_row(
            "Generation and extraction",
            "SKIP",
            "Use --llm to run live model checks.",
        )
        console.print(table)
        return

    started = perf_counter()

    try:
        model = create_model(settings)
        response = model.invoke(
            "Reply with one short sentence confirming you are ready."
        )

        if not isinstance(response.content, str) or not response.content.strip():
            raise ValueError("No text response.")

    except Exception as error:
        table.add_row(
            "Text generation",
            "FAIL",
            f"Request failed ({type(error).__name__}).",
        )
        console.print(table)
        raise typer.Exit(code=1)

    table.add_row(
        "Text generation",
        "PASS",
        f"Received text in {perf_counter() - started:.2f} seconds.",
    )

    started = perf_counter()

    try:
        gateway = ModelGateway(settings)
        proposal = gateway.extract_progress(
            "I completed the LangChain tutorial. "
            "The tool-calling exercise is still pending."
        )

        statuses = sorted(
            activity.status for activity in proposal.activities
        )

        if statuses != ["completed", "pending"]:
            raise ValueError("Unexpected extracted activities.")

    except Exception as error:
        table.add_row(
            "Structured extraction",
            "FAIL",
            f"Check failed ({type(error).__name__}).",
        )
        console.print(table)
        raise typer.Exit(code=1)

    table.add_row(
        "Structured extraction",
        "PASS",
        f"Sample extraction passed in {perf_counter() - started:.2f} seconds.",
    )
    console.print(table)


if __name__ == "__main__":
    app()