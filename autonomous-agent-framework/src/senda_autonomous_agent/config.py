from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def secret_from_env(reference: str) -> str:
    prefix, _, name = reference.partition(":")
    if prefix != "env" or not name:
        raise ValueError("Secret references use env:VARIABLE_NAME")
    if not (value := os.environ.get(name)):
        raise ValueError(f"Environment variable {name} is required")
    return value


class MCPConfig(Config):
    name: str = Field(min_length=1)
    transport: Literal["stdio", "streamable_http"]
    allowed_tools: list[str] = Field(min_length=1)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    api_key_ref: str | None = None
    timeout: float = Field(default=30, gt=0)

    @model_validator(mode="after")
    def check_transport(self) -> MCPConfig:
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP requires command")
        if self.transport == "streamable_http" and not self.url:
            raise ValueError("streamable_http MCP requires url")
        return self


class RAGConfig(Config):
    backend: Literal["sqlite"] = "sqlite"
    path: Path
    limit: int = Field(default=5, gt=0)


class LLMConfig(Config):
    provider: Literal["openai", "openai_compatible"] = "openai_compatible"
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key_ref: str | None = None
    timeout: float = Field(default=180, gt=0)
    system_prompt: str = ""
    mcp: list[MCPConfig] = Field(default_factory=list)
    rag: RAGConfig | None = None

    def api_key(self) -> str:
        if self.api_key_ref:
            return secret_from_env(self.api_key_ref)
        if self.provider == "openai":
            return secret_from_env("env:OPENAI_API_KEY")
        if self.base_url and urlsplit(self.base_url).hostname in {"127.0.0.1", "localhost", "::1"}:
            return "ollama"
        raise ValueError("OpenAI-compatible connections require base_url and api_key_ref, except loopback")

    @model_validator(mode="after")
    def check_connections(self) -> LLMConfig:
        if self.provider == "openai_compatible" and not self.base_url:
            raise ValueError("openai_compatible requires base_url")
        names = [server.name for server in self.mcp]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")
        return self


class LLMOverrides(Config):
    provider: Literal["openai", "openai_compatible"] | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_ref: str | None = None
    timeout: float | None = None
    system_prompt: str | None = None


Role = Literal["planner", "worker", "goal_judge"]


class AgentLLMConfig(Config):
    default: LLMConfig
    overrides: dict[Role, LLMOverrides] = Field(default_factory=dict)

    def for_role(self, role: Role) -> LLMConfig:
        override = self.overrides.get(role)
        if override is None:
            return self.default
        return LLMConfig.model_validate({**self.default.model_dump(), **override.model_dump(exclude_unset=True)})


class MongoConfig(Config):
    uri_ref: str = "env:SENDA_MONGODB_URI"
    database: str = Field(default="senda_agent", min_length=1)
    collection: str = Field(default="agent_events", min_length=1)
    retries: int = Field(default=2, ge=0)
    timeout_ms: int = Field(default=2000, gt=0)


class AgentConfig(Config):
    llm: AgentLLMConfig
    max_replans: int = Field(default=-1, ge=-1, strict=True)
    facts_dir: Path = Path("facts")
    project: str = "senda-autonomous-agent"
    environment: str = "dev"
    mongo: MongoConfig | None = None
    hook_exporters: list[dict[str, JsonValue]] = Field(default_factory=list)
