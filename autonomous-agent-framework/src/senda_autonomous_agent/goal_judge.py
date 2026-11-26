from __future__ import annotations

from .contracts import CriterionCheck, GoalDecision, ReplanArgs, RunState, WorkerReview
from .model import JSONModel
from .planner import replan_api_catalog


class GoalJudge:
    def __init__(self, model: JSONModel):
        self.model = model

    async def check_criterion(self, state: RunState, criterion: str, review: WorkerReview) -> CriterionCheck:
        result = await self.model.ask(
            CriterionCheck,
            {"criterion": criterion, **self._evidence(state, review)},
            role="goal_judge",
            prompt_name="check_criterion",
        )
        state.validate_fact_ids(result.fact_ids)
        return result

    async def evaluate_goal(self, state: RunState, review: WorkerReview,
                            checks: dict[str, CriterionCheck]) -> GoalDecision:
        result = await self.model.ask(
            GoalDecision,
            {
                **self._evidence(state, review),
                "criterion_checks": {name: check.model_dump() for name, check in checks.items()},
                "planner_apis": replan_api_catalog(),
            },
            role="goal_judge",
        )
        state.validate_fact_ids(result.fact_ids)
        if result.next_call:
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
