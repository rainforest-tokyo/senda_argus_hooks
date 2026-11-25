from __future__ import annotations

from .contracts import Plan, ReplanArgs, RunState
from .model import JSONModel


REPLAN_APIS = {
    "revise_plan": "Revise the remaining work using the collection problems and facts already available.",
    "rebuild_plan": "Build a new plan for the same goal when the previous strategy is not useful.",
}


class Planner:
    def __init__(self, model: JSONModel):
        self.model = model

    async def create_plan(self, state: RunState, worker_apis: dict) -> Plan:
        return await self._plan(state, worker_apis, "Create an ordered collection plan for the goal.")

    async def revise_plan(self, state: RunState, worker_apis: dict) -> Plan:
        return await self._plan(state, worker_apis, REPLAN_APIS["revise_plan"])

    async def rebuild_plan(self, state: RunState, worker_apis: dict) -> Plan:
        return await self._plan(state, worker_apis, REPLAN_APIS["rebuild_plan"])

    async def _plan(self, state: RunState, worker_apis: dict, instruction: str) -> Plan:
        payload = {
            "purpose_and_goal": state.goal.model_dump(),
            "worker_apis": worker_apis,
            "facts": [fact.model_dump() for fact in state.facts],
            "attempts": [attempt.model_dump() for attempt in state.attempts],
            "previous_plan": state.plan.model_dump() if state.plan else None,
            "replan_request": state.replan.model_dump() if state.replan else None,
        }
        result = await self.model.ask(
            Plan,
            instruction + " Use the listed worker APIs and their argument schemas. "
            "Choose useful sources, order their calls, and name the evidence expected from each call. "
            "Reuse existing facts. On replan focus on outstanding work and revise failed searches. "
            "An empty list is appropriate if the existing evidence is sufficient or no collection API is available.",
            payload,
        )
        for step in result.steps:
            if step.api not in worker_apis:
                raise ValueError(f"Plan references an unavailable worker API: {step.api}")
        return result


def replan_api_catalog(*, from_worker: bool = False) -> dict:
    names = ("revise_plan",) if from_worker else tuple(REPLAN_APIS)
    return {name: {"description": REPLAN_APIS[name], "arguments": ReplanArgs.model_json_schema()} for name in names}
