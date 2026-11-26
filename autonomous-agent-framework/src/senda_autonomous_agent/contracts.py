from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Goal(Contract):
    purpose: NonEmpty
    goal: NonEmpty
    criteria: list[NonEmpty] = Field(default_factory=list)


class APICall(Contract):
    api: NonEmpty
    arguments: dict[str, JsonValue]


class Step(APICall):
    expected_data: NonEmpty


class Plan(Contract):
    steps: list[Step]


class PythonTransformArgs(Contract):
    task: NonEmpty
    input_fact_ids: list[NonEmpty] | None = Field(
        default=None, min_length=1, description="Use all facts available when this step runs if omitted or null.",
    )
    required_output_keys: list[NonEmpty] = Field(default_factory=list)


class GeneratedPython(Contract):
    code: str = Field(min_length=1, max_length=16_000, description="Complete Python source without Markdown fences.")


class PlanningStrategy(Contract):
    direction: NonEmpty
    required_evidence: list[NonEmpty]
    selected_tool_ids: list[NonEmpty]


class WorkerReview(Contract):
    findings: list[NonEmpty]
    fact_ids: list[NonEmpty]
    reason: NonEmpty
    next_call: APICall


class CriterionArgs(Contract):
    criterion: NonEmpty


class NoArgs(Contract):
    pass


class ReplanArgs(Contract):
    focus: NonEmpty | None = None
    scope: Literal["execution", "strategy"] = "execution"


class CriterionCheck(Contract):
    status: Literal["met", "unmet", "insufficient"]
    reason: NonEmpty
    fact_ids: list[NonEmpty]


class ReplanCall(APICall):
    api: Literal["revise_plan", "rebuild_plan"]


class GoalDecision(Contract):
    completed: bool
    reason: NonEmpty
    fact_ids: list[NonEmpty]
    missing_evidence: list[NonEmpty]
    next_call: ReplanCall | None

    @model_validator(mode="after")
    def check_route(self) -> GoalDecision:
        if self.completed:
            if self.next_call is not None or self.missing_evidence:
                raise ValueError("A completed goal requires null next_call and no missing evidence")
        elif self.next_call is None or not self.missing_evidence:
            raise ValueError("An incomplete goal requires missing evidence and a replan call")
        return self


class ReplanRequest(Contract):
    requested_by: Literal["worker", "goal_judge"]
    reason: NonEmpty
    missing_evidence: list[NonEmpty] = Field(default_factory=list)
    call: APICall


class Fact(Contract):
    fact_id: str
    run_id: str
    round: int
    observed_at: str
    source: str
    source_id: str | None = None
    content: JsonValue
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Attempt(Contract):
    round: int
    step: Step
    status: Literal["collected", "empty", "failed"]
    fact_ids: list[str]
    failure: str | None = None


@dataclass
class RunState:
    goal: Goal
    run_id: str
    round: int = 1
    plan: Plan | None = None
    facts: list[Fact] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    replan: ReplanRequest | None = None
    strategy: PlanningStrategy | None = None

    def validate_fact_ids(self, fact_ids: list[str]) -> None:
        unknown = set(fact_ids) - {fact.fact_id for fact in self.facts}
        if unknown:
            raise ValueError("LLM output referenced unknown facts")


class RunResult(Contract):
    run_id: str
    status: Literal["completed", "incomplete"]
    rounds: int
    reason: str
    fact_ids: list[str]
    missing_evidence: list[str]
    facts_path: Path
