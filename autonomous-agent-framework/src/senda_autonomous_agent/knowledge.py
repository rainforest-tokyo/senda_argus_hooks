from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

import yaml
from pydantic import Field, JsonValue
from senda_argus_hooks.core.event import utc_now_iso

from .config import RAGConfig
from .contracts import Contract, Fact, NonEmpty, RunState


class Document(Contract):
    id: NonEmpty
    title: NonEmpty
    body: str
    source: NonEmpty
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class SearchFilter(Contract):
    field: Literal["tags", "severity"]
    operator: Literal["contains_all", "equals"]
    value: NonEmpty | list[NonEmpty]


class SearchArgs(Contract):
    keywords: list[NonEmpty] = Field(default_factory=list)
    filters: list[SearchFilter] = Field(default_factory=list)


class SQLiteRetriever:
    def __init__(self, config: RAGConfig):
        self.config = config

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.config.path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection

    def describe_search(self) -> dict:
        fields = {}
        with closing(self._connect()) as db:
            for name, path, operator, value_type, description in (
                ("tags", "$.tags", "contains_all", "array[string]", "Document topics"),
                ("severity", "$.severity", "equals", "string", "Source-assigned severity"),
            ):
                rows = db.execute(
                    "SELECT DISTINCT item.value FROM documents, json_each(metadata, ?) AS item "
                    "WHERE item.type = 'text' ORDER BY item.value LIMIT 41",
                    (path,),
                )
                values = [row[0] for row in rows]
                if values:
                    fields[name] = {
                        "description": description,
                        "type": value_type,
                        "operators": [operator],
                        "values" if len(values) <= 40 else "examples": values[:40],
                    }
        return {
            "keywords": "All terms match title or body by substring. Omit keywords for metadata-only search.",
            "metadata": fields,
            "limit": self.config.limit,
        }

    def search(self, args: SearchArgs, catalog: dict) -> list[Document]:
        conditions = []
        params = []
        for keyword in args.keywords:
            conditions.append("(instr(lower(title), lower(?)) > 0 OR instr(lower(body), lower(?)) > 0)")
            params.extend((keyword, keyword))
        for item in args.filters:
            field = catalog["metadata"].get(item.field)
            if not field or item.operator not in field["operators"]:
                raise ValueError("Unsupported RAG metadata field or operator")
            if item.field == "severity" and isinstance(item.value, str):
                conditions.append("json_extract(metadata, '$.severity') = ?")
                params.append(item.value)
            elif item.field == "tags" and isinstance(item.value, list) and item.value:
                for tag in item.value:
                    conditions.append("EXISTS (SELECT 1 FROM json_each(metadata, '$.tags') WHERE value = ?)")
                    params.append(tag)
            else:
                raise ValueError("Invalid value for RAG metadata filter")
        query = "SELECT id, title, body, source, metadata FROM documents"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id LIMIT ?"
        with closing(self._connect()) as db:
            return [
                Document(id=row["id"], title=row["title"], body=row["body"], source=row["source"],
                         metadata=json.loads(row["metadata"]))
                for row in db.execute(query, (*params, self.config.limit))
            ]


def read_document(path: Path) -> Document:
    source = str(path.resolve())
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() not in {".yaml", ".yml"}:
        return Document(id=source, title=path.stem, body=text, source=source)

    raw = yaml.safe_load(text)
    if not isinstance(raw, dict) or not isinstance(raw.get("info"), dict):
        raise ValueError(f"YAML must contain id and an info object: {path}")
    info = raw["info"]
    metadata: dict[str, JsonValue] = {}
    tags = info.get("tags")
    if isinstance(tags, str):
        tags = tags.split(",")
    if isinstance(tags, list):
        metadata["tags"] = list(dict.fromkeys(tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()))
    if isinstance(info.get("severity"), str):
        metadata["severity"] = info["severity"]
    description = info.get("description") or ""
    return Document(id=raw.get("id"), title=info.get("name"), body=description, source=source, metadata=metadata)


def import_documents(database: Path, documents: Iterable[Document]) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)

    def rows():
        for item in documents:
            yield item.id, item.title, item.body, item.source, json.dumps(item.metadata, ensure_ascii=False)

    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            "id TEXT NOT NULL PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, "
            "source TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}')"
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        if "metadata" not in columns:
            db.execute("ALTER TABLE documents ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")
        result = db.executemany(
            "INSERT INTO documents (id, title, body, source, metadata) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body, "
            "source=excluded.source, metadata=excluded.metadata",
            rows(),
        )
        return result.rowcount


class FactStore:
    def __init__(self, directory: Path, state: RunState):
        self.path = directory / f"{state.run_id}.jsonl"
        self.state = state

    def __enter__(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", encoding="utf-8")
        return self

    def __exit__(self, *_):
        self._file.close()

    def add(self, *, source: str, content: JsonValue, source_id: str | None = None,
            metadata: dict[str, JsonValue] | None = None) -> Fact:
        fact = Fact(fact_id=f"fact_{uuid4().hex}", run_id=self.state.run_id, round=self.state.round,
                    observed_at=utc_now_iso(), source=source, source_id=source_id, content=content,
                    metadata=metadata or {})
        self._file.write(fact.model_dump_json() + "\n")
        self._file.flush()
        self.state.facts.append(fact)
        return fact
