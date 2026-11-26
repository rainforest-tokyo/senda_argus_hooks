import asyncio
import json
from collections import deque

import pytest
from langchain_core.messages import AIMessage

from senda_autonomous_agent import (
    AgentConfig, AgentLLMConfig, AgentRunner, Goal, LLMConfig, PythonExecutionConfig, SystemPrompts, UserPrompts,
    progress_logging,
)
from senda_autonomous_agent.contracts import PlanningStrategy, RunState
from senda_autonomous_agent.knowledge import Document, SearchArgs, SQLiteRetriever, import_documents, read_document
from senda_autonomous_agent.model import JSONModel
from senda_autonomous_agent.planner import Planner
from senda_autonomous_agent.python_execution import DockerPythonRunner, PYTHON_TRANSFORM_API
from senda_autonomous_agent.tool_catalog import PYTHON_TOOL_ID, RAG_TOOL_ID


def settings(tmp_path, *, max_replans=-1, system_prompts=None, user_prompts=None):
    database = tmp_path / "docs.sqlite"
    import_documents(database, [
        Document(id="hours", title="営業時間", body="平日9時から17時", source="doc#1", metadata={"tags": ["案内"]}),
        Document(id="period", title="適用期間", body="12月末まで", source="doc#2", metadata={"tags": ["案内"]}),
        Document(id="help", title="検索の使い方", body="営業時間と適用期間を個別に検索します。", source="doc#help",
                 metadata={"doc_type": "tool_help", "tool_id": RAG_TOOL_ID, "tags": ["help"]}),
    ])
    llm = LLMConfig(
        model="qwen3-coder:30b",
        base_url="http://127.0.0.1:11434/v1",
        system_prompt="根拠を明示する。",
        system_prompts=system_prompts if system_prompts is not None else SystemPrompts(),
        user_prompts=user_prompts if user_prompts is not None else UserPrompts(),
        rag={"path": database},
    )
    return AgentConfig(llm=AgentLLMConfig(default=llm), facts_dir=tmp_path / "facts", max_replans=max_replans)


def call(api, **arguments):
    return {"api": api, "arguments": arguments}


def plan(keyword):
    return {"steps": [{**call("retrieve_from_rag", keywords=[keyword]), "expected_data": keyword}]}


def strategy():
    return {"direction": "SQLiteで根拠を探す", "required_evidence": ["窓口の案内"],
            "selected_tool_ids": [RAG_TOOL_ID]}


def review(payload, api, **arguments):
    return {"findings": [], "fact_ids": [fact["fact_id"] for fact in payload["facts"]],
            "reason": "次の操作を選択", "next_call": call(api, **arguments)}


class ScriptedDecisions:
    def __init__(self, replies):
        self.replies = deque(replies)
        self.inputs = []
        self.system_prompts = []

    async def ainvoke(self, messages, *, config):
        assert "根拠を明示する。" in messages[0].content
        self.system_prompts.append(messages[0].content)
        data = json.loads(messages[1].content)
        self.inputs.append(data)
        response = self.replies.popleft()
        return AIMessage(content=json.dumps(response(data) if callable(response) else response))


def test_shared_system_prompt_survives_role_override():
    shared = "Base your decisions on the supplied evidence."
    worker = "Review the returned reference documents."
    config = AgentLLMConfig.model_validate({
        "default": {
            "model": "qwen3-coder:30b",
            "base_url": "http://127.0.0.1:11434/v1",
            "system_prompts": {"default": shared, "worker": worker},
        },
        "overrides": {"worker": {"timeout": 60}},
    })

    worker_config = config.for_role("worker")
    assert worker_config.timeout == 60
    prompts = worker_config.system_prompts
    assert prompts.strategy_planner == prompts.execution_planner == shared
    assert prompts.goal_judge == prompts.check_criterion == shared
    assert prompts.generate_python == shared
    assert prompts.worker == worker


def test_user_prompts_render_per_phase_and_validate_additional_schema(tmp_path):
    additional_schema = {"properties": {"fact_ids": {"type": "array", "uniqueItems": True}}}
    prompts = UserPrompts(
        default="共通の依頼\n{{LLM-Plan}}",
        execution_planner="参考方針\n{{LLM-Plan}}",
        worker={"template": "参考の実行計画\n{{LLM-Plan}}", "output_schema": additional_schema},
        goal_judge="判定時の参考計画\n{{LLM-Plan}}",
    )
    config = settings(tmp_path, user_prompts=prompts)
    criterion = "営業時間を確認できる。"
    strategy_response = {**strategy(), "direction": "参考文字列 {{LLM-Plan}} を含む方針"}
    plan_response = plan("営業時間")

    def duplicate_ids(payload):
        response = review(payload, "check_criterion", criterion=criterion)
        return {**response, "fact_ids": response["fact_ids"] * 2}

    planner = ScriptedDecisions([strategy_response, plan_response])
    worker = ScriptedDecisions([
        duplicate_ids,
        lambda data: review(data, "check_criterion", criterion=criterion),
        lambda data: review(data, "evaluate_goal"),
    ])
    judge = ScriptedDecisions([
        lambda data: {"status": "met", "reason": "根拠あり", "fact_ids": [data["facts"][0]["fact_id"]]},
        lambda data: {"completed": True, "reason": "完了", "missing_evidence": [], "next_call": None,
                      "fact_ids": [data["facts"][0]["fact_id"]]},
    ])
    clients = {"planner": planner, "worker": worker, "goal_judge": judge}
    result = asyncio.run(AgentRunner(config, client_factory=lambda role, _: clients[role]).run(
        Goal(purpose="案内の確認", goal="営業時間を確認する", criteria=[criterion]),
    ))

    assert result.status == "completed" and result.rounds == 1
    assert planner.inputs[0]["task"] == "共通の依頼\nnull"
    assert json.loads(planner.inputs[1]["task"].split("\n", 1)[1]) == strategy_response
    assert planner.inputs[1]["strategy"] == strategy_response
    assert len(worker.inputs) == 3
    assert worker.inputs[0] == worker.inputs[1]
    assert worker.inputs[0]["output_schema"] == additional_schema
    assert json.loads(worker.inputs[0]["task"].split("\n", 1)[1]) == plan_response
    assert "task" not in judge.inputs[0]
    assert json.loads(judge.inputs[1]["task"].split("\n", 1)[1]) == plan_response


@pytest.mark.parametrize("prompts", [
    pytest.param(None, id="default-prompts"),
    pytest.param(SystemPrompts(
        strategy_planner="Choose a source for the reference documents.",
        execution_planner="Create ordered retrieval steps using the chosen source.",
        worker="Review the returned reference documents.",
        goal_judge="Decide whether both requested facts have been found.",
        check_criterion="Check the requested condition using evidence.",
    ), id="constructor-prompts"),
    pytest.param(SystemPrompts(
        default="Base your decisions on the supplied evidence.",
        worker="Review the returned reference documents.",
    ), id="shared-prompt"),
])
def test_both_replan_paths_preserve_facts(tmp_path, capsys, prompts):
    config = settings(tmp_path, system_prompts=prompts)
    criterion = "営業時間を確認できる。"
    planner = ScriptedDecisions([strategy(), plan("該当なし"), plan("営業時間"), strategy(), plan("適用期間")])
    worker = ScriptedDecisions([
        lambda data: review(data, "revise_plan"),
        lambda data: review(data, "evaluate_goal"),
        lambda data: review(data, "check_criterion", criterion=criterion),
        lambda data: review(data, "evaluate_goal"),
    ])
    judge = ScriptedDecisions([
        {"completed": False, "reason": "期間の情報が不足", "fact_ids": [],
         "missing_evidence": ["適用期間"], "next_call": call("retrieve_from_rag")},
        {"completed": False, "reason": "期間の情報が不足", "fact_ids": [],
         "missing_evidence": ["適用期間"], "next_call": call("rebuild_plan")},
        lambda data: {"status": "met", "reason": "営業時間の根拠あり", "fact_ids": [data["facts"][0]["fact_id"]]},
        lambda data: {"completed": True, "reason": "期間と時間を確認", "missing_evidence": [],
                      "fact_ids": [fact["fact_id"] for fact in data["facts"]], "next_call": None},
    ])
    clients = {"planner": planner, "worker": worker, "goal_judge": judge}
    with progress_logging(config):
        result = asyncio.run(AgentRunner(config, client_factory=lambda role, _: clients[role]).run(
            Goal(purpose="案内の確認", goal="営業時間と適用期間を確認する", criteria=[criterion])
        ))
    assert result.status == "completed"
    assert result.rounds == 3
    assert planner.system_prompts[0].startswith("You are the strategy planner.")
    assert planner.system_prompts[1].startswith("You are the execution planner.")
    assert all(prompt.startswith("You are the worker.") for prompt in worker.system_prompts)
    assert all(prompt.startswith("You are the goal judge.") for prompt in judge.system_prompts)
    assert len(judge.system_prompts) == 4
    configured = config.llm.default.system_prompts
    assert configured.strategy_planner in planner.system_prompts[0]
    assert configured.execution_planner in planner.system_prompts[1]
    assert all(configured.worker in prompt for prompt in worker.system_prompts)
    assert all(configured.goal_judge in judge.system_prompts[index] for index in (0, 1, 3))
    assert configured.check_criterion in judge.system_prompts[2]
    assert planner.inputs[0]["available_tools"] == [
        {"tool_id": RAG_TOOL_ID, "description": "Search local reference documents"}
    ]
    assert planner.inputs[1]["selected_tools"][0]["help_documents"][0]["source"] == "doc#help"
    assert planner.inputs[2]["replan_request"]["requested_by"] == "worker"
    assert planner.inputs[2]["replan_request"]["missing_evidence"] == ["該当なし"]
    assert planner.inputs[3]["replan_request"]["requested_by"] == "goal_judge"
    assert planner.inputs[4]["facts"][0]["source_id"] == "hours"
    assert [planner.inputs[index]["operation"] for index in (1, 2, 4)] == [
        "create_plan", "revise_plan", "rebuild_plan",
    ]
    facts = [json.loads(line) for line in result.facts_path.read_text(encoding="utf-8").splitlines()]
    assert [fact["round"] for fact in facts] == [2, 3]
    assert [fact["source_id"] for fact in facts] == ["hours", "period"]
    assert [fact["fact_id"] for fact in facts] == result.fact_ids
    progress = capsys.readouterr().out
    assert "revise_plan で再計画を依頼" in progress
    assert "rebuild_plan で再計画を依頼" in progress
    assert progress.count("候補ツールを 1 件選定") == 2
    assert progress.count("前の周回の方針を再利用") == 1
    assert all(result.run_id in line for line in progress.splitlines())


def test_python_output_validation_uses_existing_replan_path(tmp_path, monkeypatch):
    artifacts = tmp_path / "python-runs"
    config = settings(tmp_path, max_replans=1).model_copy(update={
        "python_execution": PythonExecutionConfig(artifacts_dir=artifacts),
    })
    completed_script = "print('{\"open_minutes\": 480}')"
    attempted = []

    async def execute(_runner, artifact_id, job, stdout, _stderr):
        payload = json.loads(job)
        assert [fact["source_id"] for fact in json.loads(payload["input_json"])["facts"]] == ["hours"]
        attempted.append(artifact_id)
        stdout.extend(b'{"open_minutes": 480}' if payload["code"] == completed_script else b"not JSON")
        return 0

    monkeypatch.setattr(DockerPythonRunner, "_execute", execute)
    transform = {**call(PYTHON_TRANSFORM_API, task="営業時間を分に換算", required_output_keys=["open_minutes"]),
                 "expected_data": "営業分数"}
    planner = ScriptedDecisions([
        {**strategy(), "selected_tool_ids": [RAG_TOOL_ID, PYTHON_TOOL_ID]},
        {"steps": [*plan("営業時間")["steps"], transform]},
        {"steps": [transform]},
    ])
    worker = ScriptedDecisions([
        {"code": "print('not JSON')"},
        lambda data: review(data, "revise_plan"),
        {"code": completed_script},
        lambda data: review(data, "evaluate_goal"),
    ])
    judge = ScriptedDecisions([
        lambda data: {"completed": True, "reason": "完了", "missing_evidence": [],
                      "fact_ids": [fact["fact_id"] for fact in data["facts"]], "next_call": None},
    ])
    clients = {"planner": planner, "worker": worker, "goal_judge": judge}
    result = asyncio.run(AgentRunner(config, client_factory=lambda role, _: clients[role]).run(
        Goal(purpose="営業時間の集計", goal="資料を取得して営業分数を計算"),
    ))

    assert result.status == "completed" and result.rounds == 2
    assert worker.inputs[1]["attempts"][-1]["status"] == "failed"
    assert "valid JSON object" in worker.inputs[1]["attempts"][-1]["failure"]
    assert [tool["tool_id"] for tool in planner.inputs[1]["selected_tools"]] == [RAG_TOOL_ID, PYTHON_TOOL_ID]
    source, derived = judge.inputs[0]["facts"]
    assert derived["content"] == {"open_minutes": 480}
    assert derived["metadata"]["input_fact_ids"] == [source["fact_id"]]
    first, second = [artifacts / result.run_id / name for name in attempted]
    assert (first / "stdout.log").read_text() == "not JSON"
    assert not (first / "result.json").exists()
    assert (second / "script.py").read_text() == completed_script
    assert json.loads((second / "result.json").read_text()) == derived["content"]


def test_replan_limit(tmp_path):
    config = settings(tmp_path, max_replans=0)
    clients = {
        "planner": ScriptedDecisions([strategy(), plan("該当なし")]),
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
    assert "help" not in catalog["metadata"]["tags"]["values"]
    args = SearchArgs.model_validate({"keywords": ["営業時間"], "filters": [
        {"field": "tags", "operator": "contains_all", "value": ["guide", "hours"]},
        {"field": "severity", "operator": "equals", "value": "info"},
    ]})
    assert retriever.search(args, catalog) == [document]
    assert retriever.search(SearchArgs(keywords=["営業時間"]), catalog) == [document]
    with pytest.raises(ValueError, match="Unsupported"):
        retriever.search(SearchArgs.model_validate({"filters": [
            {"field": "tags", "operator": "equals", "value": "guide"},
        ]}), catalog)


def test_execution_plan_rejects_an_unselected_mcp_tool(tmp_path):
    config = settings(tmp_path)
    reply = {"steps": [{
        **call("collect_from_mcp", server="sample", tool="other_tool", arguments={}),
        "expected_data": "窓口情報",
    }]}
    planner = Planner(JSONModel(config.llm.default, client=ScriptedDecisions([reply])))
    state = RunState(goal=Goal(purpose="窓口案内", goal="営業時間を確認"), run_id="test")
    selected = PlanningStrategy(direction="窓口情報を調べる", required_evidence=[],
                                selected_tool_ids=["mcp:sample/get_hours"])
    with pytest.raises(ValueError, match="strategy did not select"):
        asyncio.run(planner.build_plan(state, selected, [{
            "tool_id": "mcp:sample/get_hours", "api": "collect_from_mcp", "server": "sample", "tool": "get_hours",
        }]))
