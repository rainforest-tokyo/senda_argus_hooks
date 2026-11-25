from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from time import perf_counter
from typing import Any, NotRequired, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from senda_argus_hooks.core.context import new_run_id, reset_run_id, reset_trace_id, set_run_id, set_trace_id

from .config import AgentConfig, LLMConfig, Role
from .connectors import MCPTools
from .contracts import (
    CriterionArgs, CriterionCheck, Goal, GoalDecision, Plan, ReplanArgs, ReplanRequest, RunResult, RunState, WorkerReview,
)
from .goal_judge import GoalJudge
from .knowledge import FactStore, SQLiteRetriever
from .model import JSONModel
from .planner import Planner
from .progress import report
from .worker import Worker


class RoundState(TypedDict):
    run: RunState
    plan: NotRequired[Plan]
    review: NotRequired[WorkerReview]
    decision: NotRequired[GoalDecision]


class AgentRunner:
    def __init__(self, config: AgentConfig, *, client_factory: Callable[[Role, LLMConfig], Any] | None = None):
        self.config = config
        self.planner = Planner(self._model("planner", client_factory))
        self.worker_model = self._model("worker", client_factory)
        self.goal_judge = GoalJudge(self._model("goal_judge", client_factory))

    def _model(self, role: Role, factory: Callable[[Role, LLMConfig], Any] | None) -> JSONModel:
        settings = self.config.llm.for_role(role)
        return JSONModel(settings, client=factory(role, settings) if factory else None)

    async def run(self, goal: Goal) -> RunResult:
        state = RunState(goal=goal, run_id=new_run_id())
        run_token = set_run_id(state.run_id)
        trace_token = set_trace_id(f"trace_{uuid4().hex}")
        started = perf_counter()
        try:
            report(state, "実行", "agent.run.started", "エージェントを実行中", status="start")
            with FactStore(self.config.facts_dir, state) as store:
                async with AsyncExitStack() as stack:
                    sources = self.config.llm.default
                    mcp = MCPTools()
                    if sources.mcp:
                        report(state, "ワーカー", "agent.sources.started", "MCPツール一覧を取得中", status="start")
                        await mcp.open(sources.mcp, stack)
                        for name, failure in mcp.unavailable.items():
                            report(state, "ワーカー", "agent.sources.failed", f"MCPサーバー {name} を利用できません",
                                   status="error", server=name, failure=failure)
                    worker = Worker(self.worker_model, store, SQLiteRetriever(sources.rag) if sources.rag else None, mcp)
                    result = await self._run_rounds(state, worker, store)
            report(state, "実行", "agent.run.completed" if result.status == "completed" else "agent.run.incomplete",
                   "ゴールを達成" if result.status == "completed" else "再計画の上限に到達",
                   status="success" if result.status == "completed" else "incomplete", started=started)
            return result
        except asyncio.CancelledError:
            report(state, "実行", "agent.run.cancelled", "実行を中止", status="cancelled", started=started)
            raise
        except Exception as error:
            report(state, "実行", "agent.run.failed", f"実行に失敗 ({type(error).__name__})",
                   status="error", started=started)
            raise
        finally:
            reset_trace_id(trace_token)
            reset_run_id(run_token)

    async def _run_rounds(self, state: RunState, worker: Worker, store: FactStore) -> RunResult:
        graph = self._round_graph(worker)
        while True:
            result = await graph.ainvoke({"run": state})
            decision: GoalDecision | None = result.get("decision")
            if decision and decision.completed:
                return RunResult(run_id=state.run_id, status="completed", rounds=state.round, reason=decision.reason,
                                 fact_ids=decision.fact_ids, missing_evidence=[], facts_path=store.path)

            review: WorkerReview = result["review"]
            if decision:
                call = decision.next_call
                assert call is not None
                request = ReplanRequest(requested_by="goal_judge", reason=decision.reason,
                                        missing_evidence=decision.missing_evidence, call=call)
            else:
                ReplanArgs.model_validate(review.next_call.arguments)
                missing = list(dict.fromkeys(
                    attempt.step.expected_data for attempt in state.attempts
                    if attempt.round == state.round and attempt.status != "collected"
                ))
                request = ReplanRequest(requested_by="worker", reason=review.reason,
                                        missing_evidence=missing, call=review.next_call)

            if self.config.max_replans >= 0 and state.round - 1 >= self.config.max_replans:
                return RunResult(run_id=state.run_id, status="incomplete", rounds=state.round, reason=request.reason,
                                 fact_ids=[fact.fact_id for fact in state.facts],
                                 missing_evidence=request.missing_evidence, facts_path=store.path)
            role = "ワーカー" if request.requested_by == "worker" else "ゴール判定"
            report(state, role, "agent.replan.requested", f"{request.call.api} で再計画を依頼",
                   api=request.call.api, requested_by=request.requested_by)
            state.replan = request
            state.round += 1

    def _round_graph(self, worker: Worker):
        async def plan(round_state: RoundState) -> dict:
            state = round_state["run"]
            method = state.replan.call.api if state.replan else "create_plan"
            report(state, "プランナー", "agent.plan.started", f"{method} で作業手順を作成中", status="start",
                   api=method)
            started = perf_counter()
            planning_methods = {
                "create_plan": self.planner.create_plan,
                "revise_plan": self.planner.revise_plan,
                "rebuild_plan": self.planner.rebuild_plan,
            }
            state.plan = await planning_methods[method](state, worker.describe_apis())
            report(state, "プランナー", "agent.plan.completed", f"作業手順を {len(state.plan.steps)} 件作成",
                   api=method, step_count=len(state.plan.steps), started=started)
            return {"plan": state.plan}

        async def work(round_state: RoundState) -> dict:
            return {"review": await worker.execute(round_state["run"], round_state["plan"])}

        async def judge(round_state: RoundState) -> dict:
            state = round_state["run"]
            review = round_state["review"]
            checks: dict[str, CriterionCheck] = {}
            while review.next_call.api == "check_criterion":
                args = CriterionArgs.model_validate(review.next_call.arguments)
                report(state, "ゴール判定", "agent.criterion.started", "個別条件を確認中", status="start")
                result = await self.goal_judge.check_criterion(state, args.criterion, review)
                checks[args.criterion] = result
                report(state, "ゴール判定", "agent.criterion.completed", f"個別条件の判定結果 {result.status}")
                review = await worker.review_facts(state, checks=checks)
            if review.next_call.api == "revise_plan":
                return {"review": review}
            report(state, "ゴール判定", "agent.goal.started", "ゴール達成状況を判定中", status="start")
            decision = await self.goal_judge.evaluate_goal(state, review, checks)
            report(state, "ゴール判定", "agent.goal.evaluated",
                   "ゴール達成" if decision.completed else "根拠が不足",
                   completed=decision.completed, fact_ids=decision.fact_ids)
            return {"review": review, "decision": decision}

        graph = StateGraph(RoundState)
        graph.add_node("plan", plan)
        graph.add_node("work", work)
        graph.add_node("judge", judge)
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "work")
        graph.add_conditional_edges(
            "work", lambda state: state["review"].next_call.api == "revise_plan",
            {True: END, False: "judge"},
        )
        graph.add_edge("judge", END)
        return graph.compile()
