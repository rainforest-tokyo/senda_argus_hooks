from __future__ import annotations

from .contracts import Plan, PlanningStrategy, ReplanArgs, RunState
from .model import JSONModel
from .tool_catalog import ToolCatalog


REPLAN_APIS = {
    "revise_plan": "Revise the remaining work using collection problems and existing facts. "
                   "Set scope to strategy if the direction or tools need to change.",
    "rebuild_plan": "Build a new plan for the same goal when the previous strategy is not useful.",
}


class Planner:
    def __init__(self, model: JSONModel, *, strategy_system_prompt: str | None = None):
        self.model = model
        self.strategy_system_prompt = strategy_system_prompt

    async def select_tools(self, state: RunState, available_tools: list[dict[str, str]]) -> PlanningStrategy:
        strategy = await self.model.ask(
            PlanningStrategy,
            {**self._context(state), "available_tools": available_tools},
            role="strategy_planner",
            system_prompt=self.strategy_system_prompt,
        )
        registered = {tool["tool_id"] for tool in available_tools}
        if set(strategy.selected_tool_ids) - registered:
            raise ValueError("Strategy selected a tool outside the available catalog")
        strategy.selected_tool_ids = list(dict.fromkeys(strategy.selected_tool_ids))
        return strategy

    async def build_plan(self, state: RunState, strategy: PlanningStrategy, selected_tools: list[dict]) -> Plan:
        if not selected_tools:
            return Plan(steps=[])
        operation = state.replan.call.api if state.replan else "create_plan"
        result = await self.model.ask(
            Plan,
            {**self._context(state), "operation": operation, "strategy": strategy.model_dump(),
             "selected_tools": selected_tools},
            role="execution_planner",
        )
        selected_ids = set(strategy.selected_tool_ids)
        for step in result.steps:
            if ToolCatalog.tool_id_for_step(step) not in selected_ids:
                raise ValueError("Plan references a tool that the strategy did not select")
        return result

    @staticmethod
    def _context(state: RunState) -> dict:
        return {
            "purpose_and_goal": state.goal.model_dump(),
            "facts": [fact.model_dump() for fact in state.facts],
            "attempts": [attempt.model_dump() for attempt in state.attempts],
            "previous_plan": state.plan.model_dump() if state.plan else None,
            "replan_request": state.replan.model_dump() if state.replan else None,
        }


def replan_api_catalog(*, from_worker: bool = False) -> dict:
    names = ("revise_plan",) if from_worker else tuple(REPLAN_APIS)
    return {name: {"description": REPLAN_APIS[name], "arguments": ReplanArgs.model_json_schema()} for name in names}
