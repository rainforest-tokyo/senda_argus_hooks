import asyncio
import json
from collections import deque

import pytest
from langchain_core.messages import AIMessage

from senda_autonomous_agent import AgentConfig, AgentRunner, Goal, progress_logging
from senda_autonomous_agent.knowledge import Document, SearchArgs, SQLiteRetriever, import_documents, read_document


def settings(tmp_path, *, max_replans=-1):
    database = tmp_path / "docs.sqlite"
    import_documents(database, [
        Document(id="hours", title="営業時間", body="平日9時から17時", source="doc#1", metadata={"tags": ["案内"]}),
        Document(id="period", title="適用期間", body="12月末まで", source="doc#2", metadata={"tags": ["案内"]}),
    ])
    return AgentConfig.model_validate({
        "llm": {"default": {"model": "qwen3-coder:30b", "base_url": "http://127.0.0.1:11434/v1",
                            "system_prompt": "根拠を明示する。", "rag": {"path": database}}},
        "facts_dir": tmp_path / "facts",
        "max_replans": max_replans,
    })


def call(api, **arguments):
    return {"api": api, "arguments": arguments}


def plan(keyword):
    return {"steps": [{**call("retrieve_from_rag", keywords=[keyword]), "expected_data": keyword}]}


def review(payload, api):
    return {"findings": [], "fact_ids": [fact["fact_id"] for fact in payload["facts"]],
            "reason": "次の操作を選択", "next_call": call(api)}


class ScriptedDecisions:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.inputs = []

    async def ainvoke(self, messages, *, config):
        assert "根拠を明示する。" in messages[0].content
        data = json.loads(messages[1].content)
        self.inputs.append(data)
        response = self.replies.popleft()
        return AIMessage(content=json.dumps(response(data) if callable(response) else response))


def test_both_replan_paths_preserve_facts(tmp_path, capsys):
    config = settings(tmp_path)
    planner = ScriptedDecisions([plan("該当なし"), plan("営業時間"), plan("適用期間")])
    worker = ScriptedDecisions([
        lambda data: review(data, "revise_plan"),
        lambda data: review(data, "evaluate_goal"),
        lambda data: review(data, "evaluate_goal"),
    ])
    judge = ScriptedDecisions([
        {"completed": False, "reason": "期間の情報が不足", "fact_ids": [],
         "missing_evidence": ["適用期間"], "next_call": call("rebuild_plan")},
        lambda data: {"completed": True, "reason": "期間と時間を確認", "missing_evidence": [],
                      "fact_ids": [fact["fact_id"] for fact in data["facts"]], "next_call": None},
    ])
    clients = {"planner": planner, "worker": worker, "goal_judge": judge}
    with progress_logging(config):
        result = asyncio.run(AgentRunner(config, client_factory=lambda role, _: clients[role]).run(
            Goal(purpose="案内の確認", goal="営業時間と適用期間を確認する")
        ))
    assert result.status == "completed"
    assert result.rounds == 3
    assert planner.inputs[1]["replan_request"]["requested_by"] == "worker"
    assert planner.inputs[1]["replan_request"]["missing_evidence"] == ["該当なし"]
    assert planner.inputs[2]["replan_request"]["requested_by"] == "goal_judge"
    assert planner.inputs[2]["facts"][0]["source_id"] == "hours"
    facts = [json.loads(line) for line in result.facts_path.read_text(encoding="utf-8").splitlines()]
    assert [fact["round"] for fact in facts] == [2, 3]
    assert [fact["fact_id"] for fact in facts] == result.fact_ids
    progress = capsys.readouterr().out
    assert "revise_plan で再計画を依頼" in progress
    assert "rebuild_plan で再計画を依頼" in progress
    assert all(result.run_id in line for line in progress.splitlines())


def test_replan_limit(tmp_path):
    config = settings(tmp_path, max_replans=0)
    clients = {
        "planner": ScriptedDecisions([plan("該当なし")]),
        "worker": ScriptedDecisions([lambda data: review(data, "revise_plan")]),
        "goal_judge": ScriptedDecisions([]),
    }
    with progress_logging(config):
        result = asyncio.run(AgentRunner(config, client_factory=lambda role, _: clients[role]).run(
            Goal(purpose="案内の確認", goal="営業時間を探す")
        ))
    assert result.status == "incomplete"
    assert result.rounds == 1
    assert result.missing_evidence == ["該当なし"]
    assert not clients["goal_judge"].inputs


def test_import_and_search_catalog(tmp_path):
    source = tmp_path / "reference.yaml"
    source.write_text("id: hours\ninfo:\n  name: Counter\n  description: 平日の営業時間\n"
                      "  tags: guide,hours\n  severity: info\n", encoding="utf-8")
    config = settings(tmp_path)
    rag = config.llm.default.rag
    document = read_document(source)
    import_documents(rag.path, [document, document])
    retriever = SQLiteRetriever(rag)
    catalog = retriever.describe_search()
    assert "hours" in catalog["metadata"]["tags"]["values"]
    args = SearchArgs.model_validate({"keywords": ["営業時間"], "filters": [
        {"field": "tags", "operator": "contains_all", "value": ["guide", "hours"]},
        {"field": "severity", "operator": "equals", "value": "info"},
    ]})
    assert retriever.search(args, catalog) == [document]
    with pytest.raises(ValueError, match="Unsupported"):
        retriever.search(SearchArgs.model_validate({"filters": [
            {"field": "tags", "operator": "equals", "value": "guide"},
        ]}), catalog)
