from __future__ import annotations

import asyncio
import json
import sqlite3
from time import perf_counter
from uuid import uuid4

from .connectors import MCPArgs, MCPCollectionError, MCPTools
from .contracts import (
    Attempt, CriterionArgs, CriterionCheck, GeneratedPython, NoArgs, Plan, PythonTransformArgs, RunState, Step,
    WorkerReview,
)
from .knowledge import FactStore, SearchArgs, SQLiteRetriever
from .model import JSONModel, ModelOutputError
from .planner import replan_api_catalog
from .progress import report
from .python_execution import DockerPythonRunner, PYTHON_TRANSFORM_API, PythonExecutionError


class Worker:
    def __init__(self, model: JSONModel, store: FactStore, retriever: SQLiteRetriever | None, mcp: MCPTools,
                 python_runner: DockerPythonRunner | None = None):
        self.model = model
        self.store = store
        self.retriever = retriever
        self.mcp = mcp
        self.python_runner = python_runner
        self.rag_catalog = retriever.describe_search() if retriever else None

    def describe_apis(self) -> dict:
        catalog = {}
        if self.rag_catalog is not None:
            catalog["retrieve_from_rag"] = {
                "description": "Search local reference documents",
                "arguments": SearchArgs.model_json_schema(),
                **self.rag_catalog,
            }
        if tools := self.mcp.describe():
            catalog["collect_from_mcp"] = {
                "description": "Collect evidence with a listed read-only source tool",
                "arguments": MCPArgs.model_json_schema(),
                "tools": tools,
            }
        if self.python_runner is not None:
            catalog[PYTHON_TRANSFORM_API] = {
                "description": "Transform or aggregate collected facts with Python in an offline container",
                "arguments": PythonTransformArgs.model_json_schema(),
                "instructions": "Plan the transformation after its input collection steps. Describe the transformation "
                                "in task and the expected JSON in expected_data. Set required_output_keys to the "
                                "expected top-level keys. Use null or omit input_fact_ids to include facts collected "
                                "earlier in this plan. The worker generates the script at execution time.",
            }
        return catalog

    async def execute(self, state: RunState, plan: Plan) -> WorkerReview:
        for index, step in enumerate(plan.steps):
            attempt = await self._execute_step(state, step)
            state.attempts.append(attempt)
            remaining = plan.steps[index + 1:]
            if attempt.status != "collected" and remaining:
                review = await self.review_facts(state, remaining=remaining)
                if review.next_call.api != "continue_work":
                    return review
        return await self.review_facts(state)

    async def _execute_step(self, state: RunState, step: Step) -> Attempt:
        started = perf_counter()
        report(state, "ワーカー", "agent.step.started", f"{step.api} で情報を収集中",
               status="start", api=step.api)
        try:
            if step.api == "retrieve_from_rag" and self.retriever and self.rag_catalog is not None:
                documents = self.retriever.search(SearchArgs.model_validate(step.arguments), self.rag_catalog)
                fact_ids = [self.store.add(
                    source=doc.source, source_id=doc.id, content={"title": doc.title, "body": doc.body},
                    metadata=doc.metadata,
                ).fact_id for doc in documents]
            elif step.api == "collect_from_mcp":
                args = MCPArgs.model_validate(step.arguments)
                content = await self.mcp.collect(args)
                fact_ids = []
                if content not in (None, "", [], {}):
                    fact_ids.append(self.store.add(source=f"mcp:{args.server}/{args.tool}", content=content).fact_id)
            elif step.api == PYTHON_TRANSFORM_API and self.python_runner is not None:
                fact_ids = [await self._transform_with_python(state, step)]
            else:
                raise ValueError("Unavailable collection API")
        except (ValueError, sqlite3.Error) as error:
            failure = str(error) if isinstance(error, (MCPCollectionError, PythonExecutionError)) else type(error).__name__
            report(state, "ワーカー", "agent.step.failed", f"{step.api} の収集に失敗",
                   status="error", api=step.api, failure=failure, started=started)
            diagnostic = f"{failure}: {error.detail}" if isinstance(error, PythonExecutionError) and error.detail else failure
            return Attempt(round=state.round, step=step, status="failed", fact_ids=[], failure=diagnostic)
        report(state, "ワーカー", "agent.step.completed",
               f"{step.api} でファクトを {len(fact_ids)} 件取得" if fact_ids else f"{step.api} の該当データは0件",
               api=step.api, fact_count=len(fact_ids), started=started)
        return Attempt(round=state.round, step=step, status="collected" if fact_ids else "empty", fact_ids=fact_ids)

    async def _transform_with_python(self, state: RunState, step: Step) -> str:
        assert self.python_runner is not None
        try:
            arguments = PythonTransformArgs.model_validate(step.arguments)
            state.validate_fact_ids(arguments.input_fact_ids or [])
        except ValueError as error:
            raise PythonExecutionError("Invalid Python transform arguments", detail=str(error)[:1024]) from error
        input_ids = set(arguments.input_fact_ids or ())
        facts = [fact for fact in state.facts if not input_ids or fact.fact_id in input_ids]
        if not facts:
            raise PythonExecutionError("Python processing requires at least one collected fact")
        input_json = self.python_runner.prepare_input(facts)

        report(state, "ワーカー", "agent.python.generation.started", "ファクトを加工するPythonを生成中", status="start")
        try:
            script = await self.model.ask(
                GeneratedPython,
                {
                    "purpose_and_goal": state.goal.model_dump(),
                    "task": arguments.task,
                    "expected_data": step.expected_data,
                    "required_output_keys": arguments.required_output_keys,
                    "input": json.loads(input_json),
                    "execution_instructions": "Read input.json in the current directory. It has the same format as "
                                              "input above. Use the Python 3.12 standard library. Compute the result "
                                              "from the file contents and print exactly one JSON object to stdout. "
                                              "Use stderr for diagnostics. The container has no network.",
                },
                role="worker", prompt_name="generate_python",
            )
        except ModelOutputError as error:
            raise PythonExecutionError("LLM did not return Python source in the required JSON format") from error

        artifact_id = f"python_{uuid4().hex}"
        execution_started = perf_counter()
        report(state, "ワーカー", "agent.python.execution.started", f"隔離環境でPythonを実行中 ({artifact_id})",
               status="start", artifact_id=artifact_id)
        try:
            content = await self.python_runner.run(
                state.run_id, artifact_id, script.code, input_json, arguments.required_output_keys,
            )
        except asyncio.CancelledError:
            report(state, "ワーカー", "agent.python.execution.cancelled", f"Pythonの実行を中止 ({artifact_id})",
                   status="cancelled", started=execution_started, artifact_id=artifact_id)
            raise
        except (OSError, PythonExecutionError) as error:
            failure = error if isinstance(error, PythonExecutionError) else PythonExecutionError(
                "Could not start Python or save its artifacts", detail=type(error).__name__,
            )
            report(state, "ワーカー", "agent.python.execution.failed", f"Pythonの実行に失敗 ({artifact_id})",
                   status="error", started=execution_started, artifact_id=artifact_id,
                   exit_code=failure.exit_code, failure=str(failure))
            if failure is error:
                raise
            raise failure from error

        fact = self.store.add(
            source=f"python:{artifact_id}", source_id=artifact_id, content=content,
            metadata={"derived": True, "input_fact_ids": [item.fact_id for item in facts], "artifact_id": artifact_id},
        )
        report(state, "ワーカー", "agent.python.execution.completed", f"Pythonの結果を保存 ({artifact_id})",
               started=execution_started, artifact_id=artifact_id, fact_id=fact.fact_id, exit_code=0)
        return fact.fact_id

    async def review_facts(self, state: RunState, *, remaining: list[Step] | None = None,
                           checks: dict[str, CriterionCheck] | None = None) -> WorkerReview:
        remaining = remaining or []
        checks = checks or {}
        apis = {
            "evaluate_goal": {"description": "Request a verdict on the overall goal", "arguments": NoArgs.model_json_schema()},
            **replan_api_catalog(from_worker=True),
        }
        available_criteria = [criterion for criterion in state.goal.criteria if criterion not in checks]
        if available_criteria:
            apis["check_criterion"] = {
                "description": "Ask the goal judge to check one criterion before the full verdict",
                "arguments": CriterionArgs.model_json_schema(),
                "criteria": available_criteria,
            }
        if remaining:
            apis["continue_work"] = {
                "description": "Continue the remaining planned collection steps", "arguments": NoArgs.model_json_schema()
            }
        report(state, "ワーカー", "agent.review.started", "収集結果を精査中", status="start")
        review = await self.model.ask(
            WorkerReview,
            {
                "purpose_and_goal": state.goal.model_dump(),
                "strategy": state.strategy.model_dump() if state.strategy else None,
                "current_plan": state.plan.model_dump() if state.plan else None,
                "facts": [fact.model_dump() for fact in state.facts],
                "attempts": [attempt.model_dump() for attempt in state.attempts if attempt.round == state.round],
                "remaining_steps": [step.model_dump() for step in remaining],
                "criterion_checks": {name: result.model_dump() for name, result in checks.items()},
                "available_apis": apis,
            },
            role="worker",
            llm_plan=state.plan,
        )
        state.validate_fact_ids(review.fact_ids)
        call = review.next_call
        if call.api not in apis:
            raise ValueError(f"Worker selected an unavailable API: {call.api}")
        if call.api == "check_criterion":
            criterion = CriterionArgs.model_validate(call.arguments).criterion
            if criterion not in available_criteria:
                raise ValueError("Worker selected an unavailable criterion")
        elif call.api != "revise_plan":
            NoArgs.model_validate(call.arguments)
        report(state, "ワーカー", "agent.review.completed", f"次の操作として {call.api} を選択", api=call.api)
        return review
