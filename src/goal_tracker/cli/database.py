import sqlite3
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from goal_tracker.config.paths import get_app_paths
from goal_tracker.config.settings import load_settings
from goal_tracker.db.backup import backup_database


database_app = typer.Typer(
    help="Database maintenance commands.",
    no_args_is_help=True,
)
console = Console()


@database_app.command()
def backup(
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
    """Back up an existing database without changing its schema."""
    try:
        settings = load_settings(
            str(env_file) if env_file is not None else None
        )
        paths = get_app_paths(settings.data_dir)

        backup_path = backup_database(
            database_path=paths.database,
            backup_dir=paths.backups,
        )

    except FileNotFoundError:
        console.print(
            "The source database does not exist. "
            "Start the application to initialize it first."
        )
        raise typer.Exit(code=1)

    except (ValueError, OSError, sqlite3.Error) as error:
        console.print(
            f"Backup failed ({type(error).__name__}). "
            "Check configuration, database access and storage permissions."
        )
        raise typer.Exit(code=1)

    console.print("Backup created.", style="green")
    console.print(str(backup_path), markup=False)