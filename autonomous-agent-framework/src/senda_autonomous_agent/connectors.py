from __future__ import annotations

from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from pydantic import JsonValue

from .config import MCPConfig, secret_from_env
from .contracts import Contract, NonEmpty


class MCPArgs(Contract):
    server: NonEmpty
    tool: NonEmpty
    arguments: dict[str, JsonValue]


class MCPCollectionError(ValueError):
    pass


class MCPTools:
    def __init__(self):
        self._tools: dict[tuple[str, str], tuple[Any, Any]] = {}
        self.unavailable: dict[str, str] = {}

    async def open(self, servers: list[MCPConfig], stack: AsyncExitStack) -> None:
        if not servers:
            return
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as error:
            raise ValueError("Install the mcp extra to use MCP sources") from error

        for config in servers:
            timeout = timedelta(seconds=config.timeout)
            try:
                async with AsyncExitStack() as server_stack:
                    if config.transport == "stdio":
                        env = None
                        if config.api_key_ref:
                            env = {config.api_key_ref.split(":", 1)[-1]: secret_from_env(config.api_key_ref)}
                        reader, writer = await server_stack.enter_async_context(stdio_client(
                            StdioServerParameters(command=config.command, args=config.args, env=env)
                        ))
                    else:
                        headers = None
                        if config.api_key_ref:
                            headers = {"Authorization": f"Bearer {secret_from_env(config.api_key_ref)}"}
                        reader, writer, _ = await server_stack.enter_async_context(
                            streamablehttp_client(config.url, headers=headers, timeout=timeout)
                        )
                    session = await server_stack.enter_async_context(
                        ClientSession(reader, writer, read_timeout_seconds=timeout)
                    )
                    await session.initialize()
                    discovered = {}
                    cursor = None
                    seen = set()
                    while True:
                        response = await session.list_tools(cursor=cursor)
                        for tool in response.tools:
                            if tool.name in config.allowed_tools and tool.annotations and tool.annotations.readOnlyHint is True:
                                discovered[(config.name, tool.name)] = (session, tool)
                        cursor = response.nextCursor
                        if not cursor:
                            break
                        if cursor in seen:
                            raise MCPCollectionError("MCP tool pagination repeated a cursor")
                        seen.add(cursor)
                    if not discovered:
                        self.unavailable[config.name] = "No configured read-only collection tools found"
                    else:
                        stack.push_async_callback(server_stack.pop_all().aclose)
                        self._tools.update(discovered)
            except Exception as error:
                self.unavailable[config.name] = type(error).__name__

    def describe(self) -> list[dict]:
        return [
            {"server": server, "tool": name, "description": tool.description, "input_schema": tool.inputSchema}
            for (server, name), (_, tool) in self._tools.items()
        ]

    async def collect(self, arguments: MCPArgs) -> JsonValue:
        from jsonschema.exceptions import ValidationError
        from jsonschema.validators import validator_for
        from referencing import Registry

        pair = self._tools.get((arguments.server, arguments.tool))
        if pair is None:
            raise MCPCollectionError("MCP tool is outside the configured read-only collection catalog")
        session, tool = pair
        try:
            validator_for(tool.inputSchema)(tool.inputSchema, registry=Registry()).validate(arguments.arguments)
        except ValidationError:
            raise MCPCollectionError("MCP arguments do not match the published input schema") from None
        try:
            result = await session.call_tool(arguments.tool, arguments=arguments.arguments)
        except Exception as error:
            raise MCPCollectionError(type(error).__name__) from error
        if result.isError:
            raise MCPCollectionError("MCP server reported a collection failure")
        if result.structuredContent is not None:
            return result.structuredContent
        return [item.model_dump(mode="json", by_alias=True) for item in result.content]
