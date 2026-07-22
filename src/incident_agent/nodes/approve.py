from __future__ import annotations

from datetime import UTC

from incident_agent.contracts import Approval, IncidentState


def approve(state: IncidentState) -> dict[str, object]:
    """
    Approval gate before execution.

    Stage-0: auto-approve when pre-execution validation passed and there is an
    actionable ExecutionPlan. High-risk still auto-approves in dry-run mode with
    an explicit comment (live clusters should require a human approver later).
    """
    stamp = (
        state.created_at
        if state.created_at.tzinfo
        else state.created_at.replace(tzinfo=UTC)
    )
    plan = state.execution_plan
    pre_ok = state.validation_verdict is not None and state.validation_verdict.passed
    actionable = plan is not None and plan.action not in {"", "noop", "noop_investigate"}

    if pre_ok and actionable:
        approval = Approval(
            approved=True,
            by="stage0-auto-approver",
            comment=(
                f"Auto-approved dry-run for action={plan.action} "
                f"risk={state.fix_plan.risk.value if state.fix_plan else 'unknown'}"
            ),
            at=stamp,
        )
    else:
        reason = "preconditions failed or no actionable plan"
        if state.validation_verdict is not None and not state.validation_verdict.passed:
            reason = state.validation_verdict.reason
        approval = Approval(
            approved=False,
            by="stage0-auto-approver",
            comment=f"Rejected: {reason}",
            at=stamp,
        )

    log = list(state.log)
    log.append(
        f"approve: approved={approval.approved} by={approval.by} "
        f"comment={approval.comment}"
    )
    return {"approval": approval, "log": log}
