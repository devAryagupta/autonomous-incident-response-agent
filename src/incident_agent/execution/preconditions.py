"""Pre-execution safety checks against ExecutionPlan.preconditions."""

from __future__ import annotations

from incident_agent.contracts import IncidentState, ValidationVerdict


def check_preconditions(state: IncidentState) -> ValidationVerdict:
    """
    Verify preconditions before approval/execution.

    Stage-0: deterministic checks on state (no live cluster calls).
    """
    plan = state.execution_plan
    if plan is None:
        return ValidationVerdict(passed=False, reason="ExecutionPlan is missing")

    if not plan.action or plan.action == "noop_investigate":
        return ValidationVerdict(passed=False, reason="No actionable execution plan")

    if not plan.target or plan.target == "<workload>":
        # Allow synthetic demos with explicit placeholder only if target_ref was set.
        target_ref = state.observations.extra.get("target_ref")
        if not target_ref:
            return ValidationVerdict(passed=False, reason="Deployment/target does not exist")

    unmet: list[str] = []
    for precondition in plan.preconditions:
        key = precondition.lower()
        if key == "deployment exists":
            if not plan.target:
                unmet.append(precondition)
        elif key == "fix plan validated":
            if state.validation_verdict is None or not state.validation_verdict.passed:
                unmet.append(precondition)
        elif key == "rollback available":
            option = None
            if state.chosen_remediation_id:
                option = next(
                    (
                        o
                        for o in state.remediation_options
                        if o.option_id == state.chosen_remediation_id
                    ),
                    None,
                )
            if option is not None and not option.rollback_possible:
                unmet.append(precondition)
            elif plan.rollback_action is None:
                unmet.append(precondition)
        elif key == "approval required":
            # Checked later by approve node; presence of the precondition is enough here.
            continue
        elif key == "high-risk change acknowledged":
            # Stage-0: acknowledged via confidence path reaching execution.
            if state.confidence_score is not None and state.confidence_score < 0.5:
                unmet.append(precondition)

    if unmet:
        return ValidationVerdict(
            passed=False,
            reason=f"Preconditions failed: {', '.join(unmet)}",
        )
    return ValidationVerdict(
        passed=True,
        reason=f"Preconditions passed for action={plan.action} target={plan.target}",
    )
