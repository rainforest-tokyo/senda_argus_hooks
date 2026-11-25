import asyncio
import json
import sys
from contextlib import AsyncExitStack

import pytest

from senda_autonomous_agent.config import AgentConfig, MCPConfig
from senda_autonomous_agent.connectors import MCPArgs, MCPCollectionError, MCPTools
from senda_autonomous_agent.progress import progress_logging


def test_mcp_discovers_and_audits_read_only_collection(tmp_path):
    pytest.importorskip("mcp")
    server = tmp_path / "server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "from mcp.types import ToolAnnotations\n"
        "server = FastMCP('local-test')\n"
        "@server.tool(annotations=ToolAnnotations(readOnlyHint=True))\n"
        "def get_hours() -> str:\n"
        "    return '09:00-17:00'\n"
        "@server.tool()\n"
        "def unrelated_tool() -> str:\n"
        "    return 'not a collection tool'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    source = MCPConfig(name="sample", transport="stdio", command=sys.executable, args=[str(server)],
                       allowed_tools=["get_hours", "unrelated_tool"])
    events = tmp_path / "hooks.jsonl"
    config = AgentConfig.model_validate({
        "llm": {"default": {"model": "qwen3-coder:30b", "base_url": "http://127.0.0.1:11434/v1", "mcp": [source]}},
        "hook_exporters": [{"type": "jsonl", "path": str(events)}],
    })

    async def collect():
        async with AsyncExitStack() as stack:
            tools = MCPTools()
            await tools.open([source], stack)
            assert [tool["tool"] for tool in tools.describe()] == ["get_hours"], tools.unavailable
            result = await tools.collect(MCPArgs(server="sample", tool="get_hours", arguments={}))
            assert "09:00-17:00" in json.dumps(result)
            with pytest.raises(MCPCollectionError, match="outside"):
                await tools.collect(MCPArgs(server="sample", tool="unrelated_tool", arguments={}))

    with progress_logging(config):
        asyncio.run(collect())
    types = [json.loads(line)["event_type"] for line in events.read_text(encoding="utf-8").splitlines()]
    assert "mcp.tool_call.requested" in types
    assert "mcp.tool_call.completed" in types
