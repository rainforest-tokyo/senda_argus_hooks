from __future__ import annotations

import json
import time
from typing import Any, Callable

from senda_argus_hooks.core.hashing import sha256_value
from senda_argus_hooks.core.identity import derive_mcp_profile_id, derive_purpose_id, normalize_url
from senda_argus_hooks.core.runtime import emit_event, get_config
from .base import BaseInstrumentor


class ArgusSDKInstrumentor(BaseInstrumentor):
    name = "argus_sdk"

    def __init__(self):
        self._patches: list[tuple[Any, str, Callable]] = []

    def instrument(self) -> bool:
        patched = False
        try:
            from senda_argus_hooks.sdk import MockLLMClient, OllamaClient, MockMCPClient, PromptOpsClient
            candidates = [
                (MockLLMClient, "generate_answer", "llm", "mock.generate_answer"),
                (MockLLMClient, "refine_prompt", "llm", "mock.refine_prompt"),
                (OllamaClient, "chat", "llm", "ollama.chat"),
                (MockMCPClient, "call_tool", "mcp", "mcp.call_tool"),
                (PromptOpsClient, "agent_decision", "promptops", "agent.decision"),
                (PromptOpsClient, "run_completed", "promptops", "promptops.run.completed"),
            ]
        except Exception:
            candidates = []
        for cls, method_name, kind, operation in candidates:
            original = getattr(cls, method_name, None)
            if original is None or hasattr(original, "__senda_patched__"):
                continue
            wrapped = self._wrap_llm(original, operation) if kind == "llm" else (self._wrap_mcp(original, operation) if kind == "mcp" else self._wrap_promptops(original, operation))
            setattr(wrapped, "__senda_patched__", True)
            setattr(cls, method_name, wrapped)
            self._patches.append((cls, method_name, original))
            patched = True
        return patched

    def _wrap_llm(self, original: Callable, operation: str) -> Callable:
        def wrapper(obj, *args, **kwargs):
            cfg = get_config()
            start = time.perf_counter()
            input_payload = _input_payload(args, kwargs, cfg.capture_prompt)
            provider = "ollama" if operation.startswith("ollama") else "mock"
            model = kwargs.get("model") or getattr(obj, "model", provider)
            purpose = kwargs.get("purpose") or _purpose_from_args(args) or ("refiner" if "refine" in operation else "answer")
            messages_hash = sha256_value(args[0]) if args else None
            try:
                response = original(obj, *args, **kwargs)
                latency_ms = int((time.perf_counter() - start) * 1000)
                if isinstance(response, dict):
                    model = response.get("model") or model
                    purpose = response.get("purpose") or purpose
                    report, actual_tools = _extract_senda_argus_report(response)
                    if report is not None:
                        steering_detected = bool(actual_tools) and report.get("tool_name") not in {
                            (tc.get("function") or {}).get("name") for tc in actual_tools if isinstance(tc, dict)
                        }
                        emit_event(
                            "llm.tool_selection.proposed",
                            source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "provider": provider, "operation": operation},
                            data={"senda_argus_report": report, "steering_detected": steering_detected},
                            status="success",
                        )
                output_payload = _safe_response(response) if cfg.capture_response else {"response_hash": sha256_value(_safe_response(response))}
                llm_data = {"provider": provider, "operation": operation, "purpose": purpose, "model": model, "input": input_payload, "output": output_payload}
                if messages_hash:
                    llm_data["messages_hash"] = messages_hash
                usage = _extract_usage(response)
                if usage:
                    llm_data["usage"] = usage
                emit_event(
                    "llm.request",
                    source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "provider": provider, "operation": operation},
                    data={"llm": llm_data},
                    status="success",
                    latency_ms=latency_ms,
                )
                return response
            except Exception as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                emit_event(
                    "llm.error",
                    source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "provider": provider, "operation": operation},
                    data={"llm": {"provider": provider, "operation": operation, "purpose": purpose, "model": model, "input": input_payload}},
                    status="error",
                    latency_ms=latency_ms,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                )
                raise
        return wrapper

    def _wrap_mcp(self, original: Callable, operation: str) -> Callable:
        def wrapper(obj, *args, **kwargs):
            cfg = get_config()
            start = time.perf_counter()
            tool = args[0] if args else kwargs.get("tool") or kwargs.get("name")
            arguments = args[1] if len(args) > 1 else kwargs.get("arguments") or {}
            capability = kwargs.get("capability")
            server = getattr(obj, "server", "unknown")
            server_url = getattr(obj, "url", None) or getattr(obj, "base_url", None) or getattr(obj, "server_url", None)
            purpose_id = derive_purpose_id(mcp_server_name=server, mcp_server_url=server_url, tool_name=tool, capability=capability)
            mcp_profile_id = derive_mcp_profile_id(mcp_server_name=server, mcp_server_url=server_url, tools=list(getattr(obj, "tools", {}).keys()) if hasattr(obj, "tools") else [])
            args_payload = {"tool": tool, "arguments": arguments, "capability": capability}
            base_mcp = {
                "server": server,
                "server_url": normalize_url(str(server_url)) if server_url else None,
                "operation": operation,
                "tool": tool,
                "capability": capability,
                "purpose_id": purpose_id,
                "mcp_profile_id": mcp_profile_id,
                "arguments_hash": sha256_value(args_payload),
            }
            if cfg.capture_arguments:
                base_mcp["arguments"] = args_payload
            emit_event(
                "mcp.tool_call.requested",
                source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "operation": operation},
                data={"mcp": base_mcp},
                status="start",
                purpose_id=purpose_id,
            )
            try:
                response = original(obj, *args, **kwargs)
                latency_ms = int((time.perf_counter() - start) * 1000)
                raw_result = _safe_response(response)
                result_payload = raw_result if cfg.capture_result else None
                completed_mcp = dict(base_mcp)
                if result_payload is not None:
                    completed_mcp["result"] = result_payload
                completed_mcp["result_hash"] = sha256_value(raw_result)
                emit_event(
                    "mcp.tool_call.completed",
                    source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "operation": operation},
                    data={"mcp": completed_mcp},
                    status="success",
                    latency_ms=latency_ms,
                    purpose_id=purpose_id,
                )
                return response
            except Exception as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                emit_event(
                    "mcp.tool_call.failed",
                    source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "operation": operation},
                    data={"mcp": base_mcp},
                    status="error",
                    latency_ms=latency_ms,
                    error={"type": exc.__class__.__name__, "message": str(exc)},
                    purpose_id=purpose_id,
                )
                raise
        return wrapper


    def _wrap_promptops(self, original: Callable, operation: str) -> Callable:
        def wrapper(obj, *args, **kwargs):
            start = time.perf_counter()
            try:
                response = original(obj, *args, **kwargs)
                latency_ms = int((time.perf_counter() - start) * 1000)
                event_type = operation
                source = {"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "operation": operation}
                emit_event(event_type, source=source, data=response if isinstance(response, dict) else {"result": response}, status="success", latency_ms=latency_ms)
                return response
            except Exception as exc:
                latency_ms = int((time.perf_counter() - start) * 1000)
                emit_event("promptops.error", source={"component": "instrumentor", "sdk": "senda_argus_hooks.sdk", "operation": operation}, data={"args": args, "kwargs": kwargs}, status="error", latency_ms=latency_ms, error={"type": exc.__class__.__name__, "message": str(exc)})
                raise
        return wrapper

    def uninstrument(self) -> bool:
        for cls, method_name, original in self._patches:
            setattr(cls, method_name, original)
        self._patches = []
        return True


def _input_payload(args, kwargs, capture: bool) -> dict[str, Any]:
    payload = {"args": args, "kwargs": kwargs}
    return payload if capture else {"input_hash": sha256_value(payload)}


def _purpose_from_args(args) -> str | None:
    if args and isinstance(args[0], dict):
        return args[0].get("purpose")
    return None


def _extract_senda_argus_report(response: dict) -> tuple[dict | None, list]:
    """tool_calls から senda_argus_report を取り出し、残りの実ツール呼び出しを返す。

    senda_argus_report エントリは response["message"]["tool_calls"] から除去される。
    """
    msg = response.get("message")
    if not isinstance(msg, dict):
        return None, []
    tool_calls = msg.get("tool_calls")
    if not isinstance(tool_calls, list):
        return None, []
    report_args: dict | None = None
    actual_tools: list = []
    for tc in tool_calls:
        fn = tc.get("function") if isinstance(tc, dict) else None
        if isinstance(fn, dict) and fn.get("name") == "senda_argus_report":
            raw_args = fn.get("arguments") or {}
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except Exception:
                    raw_args = {}
            report_args = raw_args
        else:
            actual_tools.append(tc)
    if report_args is not None:
        msg["tool_calls"] = actual_tools
    return report_args, actual_tools


def _safe_response(response: Any) -> Any:
    for attr in ("model_dump", "dict", "json"):
        if hasattr(response, attr):
            try:
                return getattr(response, attr)()
            except Exception:
                pass
    return response


def _extract_usage(response: Any) -> dict[str, int] | None:
    """MockLLMClient/OllamaClient レスポンスからトークン使用量を抽出する。

    Ollama の /api/chat は prompt_eval_count/eval_count をトップレベルで返す。
    OpenAI 互換 usage 形状 (prompt_tokens/completion_tokens) も併せて許容する。
    """
    if not isinstance(response, dict):
        return None
    usage = response.get("usage")
    input_tokens = (usage or {}).get("prompt_tokens") if isinstance(usage, dict) else None
    output_tokens = (usage or {}).get("completion_tokens") if isinstance(usage, dict) else None
    if input_tokens is None:
        input_tokens = response.get("prompt_eval_count")
    if output_tokens is None:
        output_tokens = response.get("eval_count")
    result: dict[str, int] = {}
    if input_tokens is not None:
        result["input_tokens"] = int(input_tokens)
    if output_tokens is not None:
        result["output_tokens"] = int(output_tokens)
    return result or None
