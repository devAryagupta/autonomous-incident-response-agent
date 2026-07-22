from __future__ import annotations

from typing import Any

from incident_agent.contracts import ExecutionResult, IncidentState
from incident_agent.providers import ProviderBundle, resolve_providers


def execute_fix(
    state: IncidentState,
    *,
    providers: ProviderBundle | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """
    Execute the FixPlan via ExecutionProvider (Stage-0: dry-run only).

    Requires approval. Does not mark the incident resolved — that is verify_outcome.
    """
    if state.fix_plan is None:
        raise ValueError("state.fix_plan is required before execute_fix()")
    if state.approval is None or not state.approval.approved:
        stamp = state.created_at
        result = ExecutionResult(
            executed=False,
            success=False,
            status="skipped",
            action=state.execution_plan.action if state.execution_plan else "",
            applied_changes=[],
            summary="Execution skipped: approval not granted",
            details={"provider": "none", "reason": "not_approved"},
            started_at=stamp,
            finished_at=stamp,
        )
        log = list(state.log)
        log.append("execute_fix: skipped (not approved)")
        return {"execution": result, "log": log}

    bundle = providers or resolve_providers(config)
    result = bundle.execution.execute(state.fix_plan, state=state)
    log = list(state.log)
    log.append(
        "execute_fix: "
        f"provider={type(bundle.execution).__name__} "
        f"status={result.status} action={result.action} "
        f"success={result.success} changes={len(result.applied_changes)}"
    )
    return {"execution": result, "log": log}
