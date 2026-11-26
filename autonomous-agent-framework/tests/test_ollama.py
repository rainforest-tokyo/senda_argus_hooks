import asyncio
import json
import os
from pathlib import Path

import pytest

from senda_autonomous_agent import AgentConfig, AgentRunner, Goal, progress_logging
from senda_autonomous_agent.knowledge import import_documents, read_document


pytestmark = pytest.mark.skipif(os.getenv("SENDA_TEST_OLLAMA") != "1", reason="Enable to use local Ollama")
EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def _ollama_config(tmp_path, example, filename="ollama.json"):
    settings = json.loads((example / filename).read_text(encoding="utf-8"))
    database = tmp_path / "knowledge.sqlite"
    import_documents(database, (read_document(path) for path in sorted((example / "documents").glob("*.yaml"))))
    settings["llm"]["default"]["rag"]["path"] = str(database)
    settings["facts_dir"] = str(tmp_path / "facts")
    if "python_execution" in settings:
        settings["python_execution"]["artifacts_dir"] = str(tmp_path / "python-runs")
    settings["max_replans"] = 2
    hook_path = tmp_path / "hooks.jsonl"
    settings["hook_exporters"][0]["path"] = str(hook_path)
    return AgentConfig.model_validate(settings), hook_path


def test_qwen3_coder_openai_compatible(tmp_path, capsys):
    config, hook_path = _ollama_config(tmp_path, EXAMPLES)

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
    event_types = [event["event_type"] for event in events]
    assert event_types.index("agent.strategy.completed") < event_types.index("agent.tool_help.completed")
    assert event_types.index("agent.tool_help.completed") < event_types.index("agent.plan.started")


def test_qwen3_coder_replans_across_rounds(tmp_path):
    config, hook_path = _ollama_config(tmp_path, EXAMPLES / "replan")
    with progress_logging(config):
        result = asyncio.run(AgentRunner(config).run(Goal(
            purpose="窓口案内の確認",
            goal="平日の窓口の営業時間と、問い合わせ用メールアドレスを資料から確認する。",
            criteria=[
                "平日の開始・終了時刻を根拠付きで確認できる。",
                "問い合わせ用メールアドレスを根拠付きで確認できる。",
            ],
        )))

    facts = [json.loads(line) for line in result.facts_path.read_text(encoding="utf-8").splitlines()]
    events = [json.loads(line) for line in hook_path.read_text(encoding="utf-8").splitlines()]
    assert all(event["run_id"] == result.run_id for event in events)
    replan_events = [event["data"]["progress"] for event in events if event["event_type"] == "agent.replan.requested"]
    assert [(item["round"], item["requested_by"]) for item in replan_events] == [(1, "worker"), (2, "goal_judge")]
    decisions = [event["data"]["progress"] for event in events if event["event_type"] == "agent.goal.evaluated"]
    assert [(item["round"], item["completed"]) for item in decisions] == [(2, False), (3, True)]
    help_events = [event["data"]["progress"] for event in events if event["event_type"] == "agent.tool_help.completed"]
    assert [(item["round"], item["help_count"]) for item in help_events] == [(1, 1), (2, 1), (3, 1)]
    assert result.status == "completed" and result.rounds == 3
    assert {fact["source_id"] for fact in facts} == {"replan-office-hours", "replan-office-contact"}
    assert [fact["round"] for fact in facts] == [2, 3]
    assert set(result.fact_ids) == {fact["fact_id"] for fact in facts}


@pytest.mark.skipif(os.getenv("SENDA_TEST_DOCKER") != "1", reason="Enable to use local Docker")
def test_qwen3_coder_generates_and_executes_python(tmp_path):
    config, hook_path = _ollama_config(tmp_path, EXAMPLES, "python-ollama.json")
    with progress_logging(config):
        result = asyncio.run(AgentRunner(config).run(Goal(
            purpose="サンプルカフェの営業時間の集計",
            goal="SQLite RAGで「営業時間」を検索し、サンプルカフェの資料からPythonで平日の営業分数を計算する。",
            criteria=["Pythonの出力JSONに open_time、close_time、open_minutes があり、元資料から計算できる。"],
        )))

    assert result.status == "completed"
    facts = [json.loads(line) for line in result.facts_path.read_text(encoding="utf-8").splitlines()]
    source = next(fact for fact in facts if fact["source_id"] == "sample-cafe-hours")
    computed = next(fact for fact in facts if fact["source"].startswith("python:"))
    assert {key: computed["content"][key] for key in ("open_time", "close_time", "open_minutes")} == {
        "open_time": "09:00", "close_time": "17:00", "open_minutes": 480,
    }
    assert computed["metadata"]["input_fact_ids"] == [source["fact_id"]]
    artifact_id = computed["metadata"]["artifact_id"]
    directory = tmp_path / "python-runs" / result.run_id / artifact_id
    assert (directory / "script.py").stat().st_size > 0
    assert json.loads((directory / "result.json").read_bytes()) == computed["content"]
    events = [json.loads(line) for line in hook_path.read_text(encoding="utf-8").splitlines()]
    event = next(event for event in events if event["event_type"] == "agent.python.execution.completed")
    assert event["run_id"] == result.run_id
    assert event["data"]["progress"]["artifact_id"] == artifact_id
