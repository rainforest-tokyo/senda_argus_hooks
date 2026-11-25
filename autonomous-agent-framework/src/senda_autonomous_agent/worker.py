from __future__ import annotations

import sqlite3
from time import perf_counter

from .connectors import MCPArgs, MCPCollectionError, MCPTools
from .contracts import Attempt, CriterionArgs, CriterionCheck, NoArgs, Plan, RunState, Step, WorkerReview
from .knowledge import FactStore, SearchArgs, SQLiteRetriever
from .model import JSONModel
from .planner import replan_api_catalog
from .progress import report


class Worker:
    def __init__(self, model: JSONModel, store: FactStore, retriever: SQLiteRetriever | None, mcp: MCPTools):
        self.model = model
        self.store = store
        self.retriever = retriever
        self.mcp = mcp
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
            else:
                raise ValueError("Unavailable collection API")
        except (ValueError, sqlite3.Error) as error:
            failure = str(error) if isinstance(error, MCPCollectionError) else type(error).__name__
            report(state, "ワーカー", "agent.step.failed", f"{step.api} の収集に失敗",
                   status="error", api=step.api, failure=failure, started=started)
            return Attempt(round=state.round, step=step, status="failed", fact_ids=[], failure=failure)
        report(state, "ワーカー", "agent.step.completed",
               f"{step.api} でファクトを {len(fact_ids)} 件取得" if fact_ids else f"{step.api} の該当データは0件",
               api=step.api, fact_count=len(fact_ids), started=started)
        return Attempt(round=state.round, step=step, status="collected" if fact_ids else "empty", fact_ids=fact_ids)

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
            "Review collected evidence. Identify missing data and discrepancies. "
            "If a collection route failed or returned no data and a revised plan is needed, call revise_plan. "
            "If useful independent steps remain, choose continue_work. "
            "Call check_criterion only when a targeted check is useful; otherwise call evaluate_goal. "
            "Never repeat a previously checked criterion.",
            {
                "purpose_and_goal": state.goal.model_dump(),
                "current_plan": state.plan.model_dump() if state.plan else None,
                "facts": [fact.model_dump() for fact in state.facts],
                "attempts": [attempt.model_dump() for attempt in state.attempts if attempt.round == state.round],
                "remaining_steps": [step.model_dump() for step in remaining],
                "criterion_checks": {name: result.model_dump() for name, result in checks.items()},
                "available_apis": apis,
            },
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
