from __future__ import annotations

import sqlite3
from urllib.parse import quote

from .connectors import MCPArgs, MCPCollectionError, MCPTools
from .contracts import Step
from .knowledge import SQLiteRetriever
from .python_execution import PYTHON_TRANSFORM_API


RAG_TOOL_ID = "rag:retrieve_from_rag"
PYTHON_TOOL_ID = "local:transform_with_python"
LOCAL_TOOL_IDS = {"retrieve_from_rag": RAG_TOOL_ID, PYTHON_TRANSFORM_API: PYTHON_TOOL_ID}


def _mcp_tool_id(server: str, tool: str) -> str:
    return f"mcp:{quote(server, safe='')}/{quote(tool, safe='')}"


class ToolCatalog:
    def __init__(self, worker_apis: dict, mcp: MCPTools, retriever: SQLiteRetriever | None):
        self._mcp = mcp
        self._retriever = retriever
        self._entries: dict[str, dict] = {}
        self._help: dict[str, dict] = {}

        for api, tool_id in LOCAL_TOOL_IDS.items():
            if definition := worker_apis.get(api):
                self._entries[tool_id] = {"api": api, **definition}
        if mcp_api := worker_apis.get("collect_from_mcp"):
            for tool in mcp_api["tools"]:
                tool_id = _mcp_tool_id(tool["server"], tool["tool"])
                self._entries[tool_id] = {
                    **tool,
                    "api": "collect_from_mcp",
                    "description": tool["description"] or mcp_api["description"],
                    "arguments": mcp_api["arguments"],
                }

    def list_tools(self) -> list[dict[str, str]]:
        return [
            {"tool_id": tool_id, "description": entry["description"]}
            for tool_id, entry in self._entries.items()
        ]

    async def get_tool_help(self, tool_id: str) -> dict:
        if tool_id in self._help:
            return self._help[tool_id]
        entry = self._entries[tool_id]
        documents = []
        errors = []
        if entry["api"] == "collect_from_mcp":
            try:
                documents.extend(await self._mcp.read_help(entry["server"], entry["tool"]))
            except MCPCollectionError as error:
                errors.append(str(error))
        if self._retriever:
            try:
                documents.extend(self._retriever.tool_help(tool_id))
            except sqlite3.Error:
                errors.append("SQLite tool Help could not be read")

        result = {"tool_id": tool_id, **entry, "help_documents": documents}
        if errors:
            result["help_warnings"] = errors
        else:
            self._help[tool_id] = result
        return result

    @staticmethod
    def tool_id_for_step(step: Step) -> str:
        if step.api in LOCAL_TOOL_IDS:
            return LOCAL_TOOL_IDS[step.api]
        if step.api == "collect_from_mcp":
            args = MCPArgs.model_validate(step.arguments)
            return _mcp_tool_id(args.server, args.tool)
        raise ValueError(f"Plan references an unavailable worker API: {step.api}")
