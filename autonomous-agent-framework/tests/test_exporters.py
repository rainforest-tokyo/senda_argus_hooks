from types import SimpleNamespace

import pytest

from senda_autonomous_agent.config import MongoConfig
from senda_autonomous_agent.progress import MongoDBExporter, ProgressExporter


def test_mongo_exports_progress_and_retries_same_ids(monkeypatch, capsys):
    pymongo = pytest.importorskip("pymongo")
    writes = []

    def bulk_write(operations, *, ordered):
        writes.append(operations)
        if len(writes) == 1:
            raise TimeoutError("Simulated MongoDB timeout")

    class Client:
        def __getitem__(self, name):
            return SimpleNamespace(bulk_write=bulk_write) if name == "agent_events" else self

        def close(self):
            pass

    monkeypatch.setenv("SENDA_MONGODB_URI", "mongodb://localhost")
    monkeypatch.setattr(pymongo, "MongoClient", lambda *args, **kwargs: Client())
    event = {"event_id": "event-1", "run_id": "run-1", "data": {
        "progress": {"round": 1, "role": "ワーカー", "message": "検索中"},
    }}
    events = [event, {"event_id": "event-2", "data": {}}]
    progress = ProgressExporter()
    mongo = MongoDBExporter(MongoConfig())
    progress.export(events)
    mongo.export(events)
    mongo.shutdown()
    assert capsys.readouterr().out.strip() == "[run-1 / 周回1] ワーカー 検索中"
    assert len(writes) == 2
    assert [operation._filter for operation in writes[0]] == [{"_id": "event-1"}]
    assert writes[0][0]._doc == {"$setOnInsert": event}
