"""Pre-execution safety checks against ExecutionPlan.preconditions."""

from __future__ import annotations

from incident_agent.contracts import ExecutionPlan, IncidentState, ValidationVerdict

_KIND_ALIASES = {
    "deploy": "deployment",
    "deployment": "deployment",
    "pod": "pod",
    "pods": "pod",
}


def execution_namespace(state: IncidentState) -> str:
    """Namespace for the mutation target (ResourceRef, then observations.extra)."""
    if state.resource and state.resource.namespace:
        return state.resource.namespace
    extra_ns = state.observations.extra.get("namespace")
    if isinstance(extra_ns, str) and extra_ns:
        return extra_ns
    return "default"


def parse_execution_target(target: str) -> tuple[str, str]:
    """Return (kind, name) from ``deployment/foo``, ``pod/foo``, or a bare name."""
    raw = (target or "").strip()
    if not raw or raw == "<workload>":
        return "deployment", ""
    if "/" in raw:
        kind, name = raw.split("/", 1)
        return _KIND_ALIASES.get(kind.lower(), kind.lower() or "deployment"), name
    return "deployment", raw


def target_probe_ref(plan: ExecutionPlan) -> tuple[str, str]:
    """Kind/name the existence probe should GET for this plan."""
    kind, name = parse_execution_target(plan.target)
    if plan.action == "restart_pod":
        return "pod", name
    return kind, name


def check_preconditions(
    state: IncidentState,
    *,
    target_exists: bool | None = None,
) -> ValidationVerdict:
    """
    Verify preconditions before approval/execution.

    ``target_exists`` is the provider probe for ``deployment exists``:
    - ``None`` — Stage-0 string check only (no cluster)
    - ``True`` / ``False`` — result of ExecutionProvider.resource_exists
    """
    plan = state.execution_plan
    if plan is None:
        return ValidationVerdict(passed=False, reason="ExecutionPlan is missing")

    if not plan.action or plan.action == "noop_investigate":
        return ValidationVerdict(passed=False, reason="No actionable execution plan")

    if _missing_target_string(state, plan) and target_exists is not True:
        return ValidationVerdict(passed=False, reason="Deployment/target does not exist")

    unmet = _unmet_preconditions(state, plan, target_exists=target_exists)
    if unmet:
        return ValidationVerdict(
            passed=False,
            reason=f"Preconditions failed: {', '.join(unmet)}",
        )
    return ValidationVerdict(
        passed=True,
        reason=f"Preconditions passed for action={plan.action} target={plan.target}",
    )


def _missing_target_string(state: IncidentState, plan: ExecutionPlan) -> bool:
    if plan.target and plan.target != "<workload>":
        return False
    return not state.observations.extra.get("target_ref")


def _unmet_preconditions(
    state: IncidentState,
    plan: ExecutionPlan,
    *,
    target_exists: bool | None,
) -> list[str]:
    unmet: list[str] = []
    for precondition in plan.preconditions:
        key = precondition.lower()
        if key == "deployment exists":
            if _deployment_missing(plan, target_exists=target_exists):
                unmet.append(precondition)
        elif key == "fix plan validated":
            if state.validation_verdict is None or not state.validation_verdict.passed:
                unmet.append(precondition)
        elif key == "rollback available":
            if _rollback_missing(state, plan):
                unmet.append(precondition)
        elif key == "approval required":
            continue
        elif key == "high-risk change acknowledged":
            if state.confidence_score is not None and state.confidence_score < 0.5:
                unmet.append(precondition)
    return unmet


def _deployment_missing(plan: ExecutionPlan, *, target_exists: bool | None) -> bool:
    if target_exists is False:
        return True
    if target_exists is True:
        return False
    return not plan.target


def _rollback_missing(state: IncidentState, plan: ExecutionPlan) -> bool:
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
        return True
    return plan.rollback_action is None
