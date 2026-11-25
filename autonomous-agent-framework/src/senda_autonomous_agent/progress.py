from __future__ import annotations

import sys
from contextlib import contextmanager
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import perf_counter
from typing import Any

from senda_argus_hooks import register, shutdown
from senda_argus_hooks.core.runtime import emit_event
from senda_argus_hooks.exporters.base import BaseExporter
from senda_argus_hooks.exporters.registry import register_exporter

from .config import AgentConfig, MongoConfig, secret_from_env
from .contracts import RunState


SOURCE = {"component": "agent_framework", "sdk": "senda_autonomous_agent"}


def report(state: RunState, role: str, event_type: str, message: str, *, status: str = "success",
           started: float | None = None, **details: Any) -> None:
    emit_event(
        event_type, source={**SOURCE, "operation": role}, status=status,
        latency_ms=None if started is None else round((perf_counter() - started) * 1000),
        data={"progress": {"round": state.round, "role": role, "message": message, **details}},
    )


class ProgressExporter(BaseExporter):
    def export(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            progress = event["data"].get("progress")
            if progress:
                print(f"[{event['run_id']} / 周回{progress['round']}] "
                      f"{progress['role']} {progress['message']}", flush=True)


class MongoDBExporter(BaseExporter):
    def __init__(self, config: MongoConfig):
        try:
            from pymongo import MongoClient, UpdateOne
        except ImportError as error:
            raise ValueError("Install the mongo extra to use MongoDB logging") from error
        self.client = MongoClient(secret_from_env(config.uri_ref), timeoutMS=config.timeout_ms, connect=False)
        self.collection = self.client[config.database][config.collection]
        self.update_one = UpdateOne
        self.retries = config.retries
        self.timeout = config.timeout_ms / 1000
        self.queue: Queue[dict] = Queue(maxsize=512)
        self.stopping = Event()
        self.thread = Thread(target=self._write, name="senda-mongodb-exporter", daemon=True)
        self.thread.start()

    def export(self, events: list[dict[str, Any]]) -> None:
        if self.stopping.is_set():
            return
        dropped = 0
        for event in events:
            if event["data"].get("progress"):
                try:
                    self.queue.put_nowait(event)
                except Full:
                    dropped += 1
        if dropped:
            print(f"[MongoDB] キュー上限のため {dropped} 件を保存できませんでした", file=sys.stderr, flush=True)

    def _write(self) -> None:
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    batch = [self.queue.get(timeout=0.1)]
                except Empty:
                    continue
                for _ in range(63):
                    try:
                        batch.append(self.queue.get_nowait())
                    except Empty:
                        break
                self._write_batch(batch)
        finally:
            self.client.close()

    def _write_batch(self, events: list[dict]) -> None:
        writes = [self.update_one({"_id": event["event_id"]}, {"$setOnInsert": event}, upsert=True) for event in events]
        for attempt in range(self.retries + 1):
            try:
                self.collection.bulk_write(writes, ordered=False)
                return
            except Exception as error:
                if attempt == self.retries:
                    print(f"[MongoDB] {len(events)} 件の保存を確認できませんでした ({type(error).__name__})",
                          file=sys.stderr, flush=True)

    def shutdown(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.thread.join(timeout=max(5, (self.retries + 1) * self.timeout + 1))
        if self.thread.is_alive():
            print(f"[MongoDB] 終了時に書き込みが継続中です。待機キュー {self.queue.qsize()} 件",
                  file=sys.stderr, flush=True)


@contextmanager
def progress_logging(config: AgentConfig):
    register_exporter("agent_progress", lambda _: ProgressExporter())
    exporters: list[dict[str, Any]] = [{"type": "agent_progress"}]
    if config.mongo:
        register_exporter("agent_mongodb", lambda options: MongoDBExporter(options["config"]))
        exporters.append({"type": "agent_mongodb", "config": config.mongo})
    exporters.extend(config.hook_exporters)
    register(project=config.project, environment=config.environment, exporters=exporters,
             auto_instrument=bool(config.llm.default.mcp), instrument_openai=False,
             instrument_anthropic=False, instrument_litellm=False, instrument_ollama=False,
             instrument_bedrock=False, instrument_vertexai=False, instrument_argus_sdk=False,
             instrument_openai_agents=False)
    try:
        yield
    finally:
        shutdown()
