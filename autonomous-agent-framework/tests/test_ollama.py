import asyncio
import json
import os
from pathlib import Path

import pytest

from senda_autonomous_agent import AgentConfig, AgentRunner, Goal, progress_logging
from senda_autonomous_agent.knowledge import import_documents, read_document


@pytest.mark.skipif(os.getenv("SENDA_TEST_OLLAMA") != "1", reason="Enable to use local Ollama")
def test_qwen3_coder_openai_compatible(tmp_path, capsys):
    project = Path(__file__).resolve().parents[1]
    settings = json.loads((project / "examples/ollama.json").read_text(encoding="utf-8"))
    database = tmp_path / "knowledge.sqlite"
    import_documents(database, [read_document(project / "examples/documents/cafe-hours.yaml")])
    settings["llm"]["default"]["rag"]["path"] = str(database)
    settings["facts_dir"] = str(tmp_path / "facts")
    settings["max_replans"] = 2
    hook_path = tmp_path / "hooks.jsonl"
    settings["hook_exporters"][0]["path"] = str(hook_path)
    config = AgentConfig.model_validate(settings)

    with progress_logging(config):
        result = asyncio.run(AgentRunner(config).run(Goal(
            purpose="社内案内の確認",
            goal="SQLite RAGで「営業時間」を検索し、サンプルカフェの平日の営業時間を根拠付きで確認する。",
            criteria=["SQLite RAGから取得したファクトで平日の開始・終了時刻を確認できる。"],
        )))
    assert result.status == "completed"
    assert result.fact_ids
    facts = [json.loads(line) for line in result.facts_path.read_text(encoding="utf-8").splitlines()]
    assert any(fact["source_id"] == "sample-cafe-hours" and fact["run_id"] == result.run_id for fact in facts)
    progress = capsys.readouterr().out
    assert result.run_id in progress
    assert "ゴールを達成" in progress
    events = [json.loads(line) for line in hook_path.read_text(encoding="utf-8").splitlines()]
    assert all(event["run_id"] == result.run_id for event in events)
    assert any(event["event_type"] == "llm.request" for event in events)
