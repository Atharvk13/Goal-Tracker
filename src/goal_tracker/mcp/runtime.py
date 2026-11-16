from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from anyio.from_thread import BlockingPortal, start_blocking_portal
from pydantic import TypeAdapter

from goal_tracker.mcp.client import MCPConnection, connect_mcp


_ARGUMENTS = TypeAdapter(dict[str, Any])


class MCPRuntime:
    """Let synchronous terminal code invoke asynchronous MCP tools."""

    def __init__(
        self,
        portal: BlockingPortal,
        connection: MCPConnection,
    ):
        self._portal = portal
        self._connection = connection

    def tool_names(self) -> list[str]:
        return self._connection.tool_names()

    def call(
        self,
        name: str,
        arguments: dict | None = None,
    ) -> Any:
        serialized = _ARGUMENTS.dump_python(
            arguments if arguments is not None else {},
            mode="json",
        )

        return self._portal.call(
            self._connection.call,
            name,
            serialized,
        )


@contextmanager
def open_mcp_runtime(
    database_path: Path,
    *,
    timezone: str = "Asia/Kolkata",
) -> Iterator[MCPRuntime]:
    """Keep one MCP connection open for a synchronous workflow."""
    with start_blocking_portal(backend="asyncio") as portal:
        connection_manager = connect_mcp(
            database_path,
            timezone=timezone,
        )

        with portal.wrap_async_context_manager(
            connection_manager
        ) as connection:
            yield MCPRuntime(portal, connection)
            