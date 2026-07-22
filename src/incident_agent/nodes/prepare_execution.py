from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.execution import build_execution_plan


def prepare_execution(state: IncidentState) -> dict[str, object]:
    """Build ExecutionPlan intent from the chosen FixPlan / remediation option."""
    if state.fix_plan is None:
        raise ValueError("state.fix_plan is required before prepare_execution()")

    plan = build_execution_plan(state)
    log = list(state.log)
    log.append(
        "prepare_execution: "
        f"action={plan.action} target={plan.target} "
        f"preconditions={len(plan.preconditions)} "
        f"expected_outcome={len(plan.expected_outcome)}"
    )
    return {"execution_plan": plan, "log": log}
