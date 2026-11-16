import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastmcp.client.transports import StdioTransport
from langchain.mcp import MCPAdapter
from langchain.messages import ToolMessage
from langchain.tools import BaseTool

from goal_tracker.domain.errors import (
    AppError,
    ConflictError,
    DomainValidationError,
    DuplicateReviewRequiredError,
    NotFoundError,
    StorageError,
)


ERROR_TYPES = {
    "VALIDATION_ERROR": DomainValidationError,
    "CONFLICT": ConflictError,
    "DUPLICATE_REVIEW_REQUIRED": DuplicateReviewRequiredError,
    "NOT_FOUND": NotFoundError,
    "STORAGE_ERROR": StorageError,
}


class MCPConnection:
    """Application-owned interface to discovered LangChain MCP tools."""

    def __init__(self, tools: list[BaseTool]):
        self._tools = {tool.name: tool for tool in tools}

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    async def call(
        self,
        name: str,
        arguments: dict | None = None,
    ):
        """Invoke a known tool and unwrap its application response."""
        tool = self._tools.get(name)

        if tool is None:
            raise DomainValidationError(
                f"The MCP server does not provide tool: {name}"
            )

        try:
            message = await tool.ainvoke(
                {
                    "type": "tool_call",
                    "name": name,
                    "id": str(uuid4()),
                    "args": (
                        arguments if arguments is not None else {}
                    ),
                }
            )

        except Exception as error:
            raise StorageError(
                "The MCP tool could not be reached or executed."
            ) from error

        if not isinstance(message, ToolMessage):
            raise StorageError(
                "The MCP tool returned an unexpected message type."
            )

        if message.status == "error":
            raise StorageError(
                "The MCP server rejected the tool call. "
                "Check the tool arguments and server diagnostics."
            )

        artifact = message.artifact or {}
        response = artifact.get("structured_content")

        if not isinstance(response, dict):
            raise StorageError(
                "The MCP tool did not return structured application data."
            )

        if response.get("ok") is False:
            details = response.get("error", {})
            error_type = ERROR_TYPES.get(
                details.get("code"),
                AppError,
            )

            raise error_type(
                details.get(
                    "message",
                    "The MCP operation failed.",
                ),
                request_id=response.get("request_id"),
            )

        if response.get("ok") is not True or "data" not in response:
            raise StorageError(
                "The MCP tool returned an invalid application response."
            )

        return response["data"]


@asynccontextmanager
async def connect_mcp(
    database_path: Path,
    *,
    timezone: str = "Asia/Kolkata",
) -> AsyncIterator[MCPConnection]:
    """Launch the local server and close it when the context exits."""
    database_path = database_path.expanduser()

    if not database_path.is_absolute():
        raise DomainValidationError(
            "The database path must be absolute."
        )

    transport = StdioTransport(
        command=sys.executable,
        args=[
            "-m",
            "goal_tracker.mcp.server",
            "--database",
            str(database_path.resolve()),
            "--timezone",
            timezone,
        ],
        keep_alive=False,
    )

    async with MCPAdapter(transport) as adapter:
        tools = await adapter.list_tools()
        yield MCPConnection(tools)