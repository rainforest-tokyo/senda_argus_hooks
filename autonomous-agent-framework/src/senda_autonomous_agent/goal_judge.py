from __future__ import annotations

from .contracts import CriterionCheck, GoalDecision, ReplanArgs, RunState, WorkerReview
from .model import JSONModel
from .planner import REPLAN_APIS, replan_api_catalog


class GoalJudge:
    def __init__(self, model: JSONModel):
        self.model = model

    async def check_criterion(self, state: RunState, criterion: str, review: WorkerReview) -> CriterionCheck:
        result = await self.model.ask(
            CriterionCheck,
            "Check the named criterion against the supplied facts. "
            "Use insufficient when the relevant evidence is missing.",
            {"criterion": criterion, **self._evidence(state, review)},
        )
        state.validate_fact_ids(result.fact_ids)
        return result

    async def evaluate_goal(self, state: RunState, review: WorkerReview,
                            checks: dict[str, CriterionCheck]) -> GoalDecision:
        result = await self.model.ask(
            GoalDecision,
            "Evaluate the entire goal against all supplied facts and the completion criteria. "
            "If it is complete, return completed=true, empty missing_evidence, and null next_call. "
            "If it is incomplete, describe the evidence still needed and choose revise_plan or rebuild_plan. "
            "Use the underlying facts when judging worker findings.",
            {
                **self._evidence(state, review),
                "criterion_checks": {name: check.model_dump() for name, check in checks.items()},
                "planner_apis": replan_api_catalog(),
            },
        )
        state.validate_fact_ids(result.fact_ids)
        if result.next_call:
            if result.next_call.api not in REPLAN_APIS:
                raise ValueError("Goal judge selected an unavailable planner API")
            ReplanArgs.model_validate(result.next_call.arguments)
        return result

    @staticmethod
    def _evidence(state: RunState, review: WorkerReview) -> dict:
        return {
            "purpose_and_goal": state.goal.model_dump(),
            "current_round": state.round,
            "facts": [fact.model_dump() for fact in state.facts],
            "worker_review": review.model_dump(),
        }
