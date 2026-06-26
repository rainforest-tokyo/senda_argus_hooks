from pathlib import Path

from senda_argus_hooks import register, shutdown
from senda_argus_hooks.integrations.langchain import SendaArgusCallbackHandler


def _events(path: Path):
    import json

    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_langchain_callback_handler_emits_llm_tool_and_agent_events(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    register(project="test", exporters=[{"type": "jsonl", "path": str(path)}], capture_arguments=True, capture_result=True)
    handler = SendaArgusCallbackHandler()

    handler.on_llm_start({"name": "fake_llm"}, ["hello"], run_id="llm-1")
    handler.on_llm_end({"generations": ["hi"]}, run_id="llm-1")
    handler.on_tool_start({"name": "lookup_cve", "tool_type": "http_api"}, {"cve": "CVE-2024-3094"}, run_id="tool-1")
    handler.on_tool_end({"ok": True}, run_id="tool-1", name="lookup_cve")
    handler.on_agent_action({"tool": "lookup_cve"})
    handler.on_agent_finish({"output": "done"})
    shutdown()

    event_types = [event["event_type"] for event in _events(path)]
    assert event_types == [
        "llm.request.started",
        "llm.request",
        "tool_call.requested",
        "tool_call.completed",
        "agent.decision",
        "agent.run.completed",
    ]
    tool_events = [event for event in _events(path) if event["event_type"].startswith("tool_call")]
    assert tool_events[0]["data"]["tool"]["purpose_id"].startswith("purpose_")


def test_langchain_callback_handler_emits_error_events(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    register(project="test", exporters=[{"type": "jsonl", "path": str(path)}])
    handler = SendaArgusCallbackHandler()

    handler.on_llm_start({"name": "fake_llm"}, ["hello"], run_id="llm-err")
    handler.on_llm_error(RuntimeError("llm failed"), run_id="llm-err")
    handler.on_tool_start({"name": "run_command"}, "whoami", run_id="tool-err")
    handler.on_tool_error(ValueError("tool failed"), run_id="tool-err", name="run_command")
    shutdown()

    event_types = [event["event_type"] for event in _events(path)]
    assert "llm.error" in event_types
    assert "tool_call.failed" in event_types
