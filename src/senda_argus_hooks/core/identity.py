from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def stable_hash(value: Any, *, prefix: str, length: int = 16) -> str:
    """Return a short stable identifier for JSON-like values."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    try:
        parts = urlsplit(raw)
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((scheme, netloc, path, "", ""))
    except Exception:
        return raw.lower().rstrip("/")


def derive_agent_id(*, project: str, environment: str, sdk: str | None = None, agent_hint: str | None = None) -> str:
    """Derive an execution-origin identifier.

    This intentionally avoids using MCP server/tool alone. MCP-derived grouping is
    represented by purpose_id / mcp_profile_id so that different agent codebases
    using the same capabilities can be grouped without conflating the executor.
    """
    return stable_hash(
        {
            "project": project,
            "environment": environment,
            "sdk": sdk or "unknown",
            "agent_hint": agent_hint or "default",
        },
        prefix="agent",
    )


def derive_purpose_id(
    *,
    mcp_server_name: str | None = None,
    mcp_server_url: str | None = None,
    tool_name: str | None = None,
    capability: str | None = None,
    tool_schema_hash: str | None = None,
    tool_description_hash: str | None = None,
) -> str:
    """Derive a capability/purpose grouping id from MCP metadata.

    Agents implemented in different repositories will share this id when they use
    the same MCP server/tool/capability profile.
    """
    return stable_hash(
        {
            "mcp_server_name": mcp_server_name or "unknown",
            "mcp_server_url": normalize_url(mcp_server_url),
            "tool_name": tool_name or "unknown",
            "capability": capability or "unknown",
            "tool_schema_hash": tool_schema_hash,
            "tool_description_hash": tool_description_hash,
        },
        prefix="purpose",
    )


def derive_mcp_profile_id(*, mcp_server_name: str | None = None, mcp_server_url: str | None = None, tools: Any = None) -> str:
    return stable_hash(
        {
            "mcp_server_name": mcp_server_name or "unknown",
            "mcp_server_url": normalize_url(mcp_server_url),
            "tools": tools or [],
        },
        prefix="mcp_profile",
    )


def derive_tool_purpose_id(
    *,
    framework: str | None = None,
    tool_name: str | None = None,
    tool_type: str | None = None,
    operation: str | None = None,
    target: str | None = None,
    tool_schema_hash: str | None = None,
    tool_description_hash: str | None = None,
) -> str:
    """Derive a stable purpose id for non-MCP framework tools.

    This is intentionally based on stable tool metadata instead of per-call
    arguments, so calls with the same capability group together across agents.
    """
    return stable_hash(
        {
            "framework": framework or "unknown",
            "tool_name": tool_name or "unknown",
            "tool_type": tool_type or "unknown",
            "operation": operation or "unknown",
            "target": normalize_url(target) if target else None,
            "tool_schema_hash": tool_schema_hash,
            "tool_description_hash": tool_description_hash,
        },
        prefix="purpose",
    )


def derive_retrieval_purpose_id(
    *,
    framework: str | None = None,
    retriever_name: str | None = None,
    retriever_type: str | None = None,
    index_name: str | None = None,
    collection_name: str | None = None,
    vector_store: str | None = None,
    target: str | None = None,
) -> str:
    """Derive a stable purpose id for RAG retrieval/index access.

    This id intentionally avoids per-query text so repeated access to the same
    retriever/index groups together across agent implementations.
    """
    return stable_hash(
        {
            "framework": framework or "unknown",
            "retriever_name": retriever_name or "unknown",
            "retriever_type": retriever_type or "unknown",
            "index_name": index_name or "unknown",
            "collection_name": collection_name or "unknown",
            "vector_store": vector_store or "unknown",
            "target": normalize_url(target) if target else None,
        },
        prefix="purpose",
    )


def derive_embedding_purpose_id(
    *,
    framework: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    embedding_type: str | None = None,
) -> str:
    """Derive a stable purpose id for embedding generation/use."""
    return stable_hash(
        {
            "framework": framework or "unknown",
            "provider": provider or "unknown",
            "model": model or "unknown",
            "embedding_type": embedding_type or "text",
        },
        prefix="purpose",
    )


def runtime_metadata() -> dict[str, Any]:
    return {
        "hostname": socket.gethostname(),
        "pid": os.getpid(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }
