import argparse
import asyncio
from pathlib import Path

from goal_tracker.bootstrap import bootstrap
from goal_tracker.mcp.client import connect_mcp


async def check_connection(
    database_path: Path,
    timezone: str,
) -> None:
    async with connect_mcp(
        database_path,
        timezone=timezone,
    ) as connection:
        names = connection.tool_names()
        print("Discovered tools:", ", ".join(names))

        if "list_goals" not in names:
            raise RuntimeError(
                "The server did not advertise list_goals."
            )

        goals = await connection.call(
            "list_goals",
            {"limit": 3},
        )

        if not isinstance(goals, list):
            raise RuntimeError(
                "list_goals did not return a list."
            )

        print(f"Goal records received: {len(goals)}")

    print("MCP connection closed successfully.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check Goal Tracker's local MCP connection.",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Optional application configuration file.",
    )
    arguments = parser.parse_args()

    context = bootstrap(arguments.env_file)

    asyncio.run(
        check_connection(
            context.paths.database,
            context.settings.timezone,
        )
    )


if __name__ == "__main__":
    main()