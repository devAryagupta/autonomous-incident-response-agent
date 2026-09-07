"""Build ExecutionPlan intent from FixPlan / chosen RemediationOption."""

from __future__ import annotations

from incident_agent.contracts import ExecutionPlan, FixPlan, IncidentState, RemediationOption

_EXPECTED_BY_ACTION: dict[str, tuple[str, ...]] = {
    "increase_memory_limit": (
        "restart count decreases",
        "pod becomes healthy",
    ),
    "rollback_deployment": (
        "pod becomes healthy",
        "crashloop clears",
    ),
    "rollout_restart": (
        "pod becomes healthy",
    ),
    "restart_pod": (
        "pod becomes healthy",
    ),
    "scale_deployment": (
        "pod becomes healthy",
        "restart count decreases",
    ),
    "create_or_fix_secret": (
        "secret mount succeeds",
        "pod becomes healthy",
    ),
    "fix_secret_reference": (
        "secret mount succeeds",
        "pod becomes healthy",
    ),
    "fix_volume_mount": (
        "pod becomes healthy",
    ),
    "patch_image_tag": (
        "image pull succeeds",
        "pod becomes healthy",
    ),
    "create_image_pull_secret": (
        "image pull succeeds",
        "pod becomes healthy",
    ),
    "patch_config": (
        "pod becomes healthy",
    ),
    "patch_env_var": (
        "pod becomes healthy",
    ),
    "noop_investigate": (),
}

_ROLLBACK_BY_ACTION: dict[str, str] = {
    "increase_memory_limit": "restore_previous_limit",
    "rollback_deployment": "roll_forward_or_reapply",
    "rollout_restart": "rollout_undo",
    "restart_pod": "none",
    "scale_deployment": "restore_previous_replica_count",
    "create_or_fix_secret": "delete_or_revert_secret",
    "fix_secret_reference": "restore_previous_secret_ref",
    "fix_volume_mount": "restore_previous_mount",
    "patch_image_tag": "restore_previous_image",
    "create_image_pull_secret": "remove_image_pull_secret",
    "patch_config": "restore_previous_config",
    "patch_env_var": "unset_or_restore_env",
    "noop_investigate": "none",
}

_PRECONDITIONS_BASE = (
    "deployment exists",
    "fix plan validated",
    "approval required",
)


def decision_action(state: IncidentState, *, plan: FixPlan | None = None) -> str:
    """Canonical action identity from the decision (execution_plan / FixPlan)."""
    fix_plan = plan if plan is not None else state.fix_plan
    if fix_plan is None:
        if state.execution_plan and state.execution_plan.action:
            return state.execution_plan.action
        return "noop"
    return _resolve_action(state, fix_plan)


def _resolve_action(state: IncidentState, fix_plan: FixPlan) -> str:
    if state.chosen_remediation_id and state.remediation_options:
        for opt in state.remediation_options:
            if opt.option_id == state.chosen_remediation_id:
                return opt.action
    if fix_plan.actions:
        params = fix_plan.actions[0].params or {}
        if isinstance(params.get("action"), str) and params["action"]:
            return str(params["action"])
        return fix_plan.actions[0].action_type.value
    return "noop_investigate"


def _resolve_target(state: IncidentState, fix_plan: FixPlan) -> str:
    if fix_plan.actions and fix_plan.actions[0].target:
        return fix_plan.actions[0].target
    return str(state.observations.extra.get("target_ref", "<workload>"))


def _chosen_option(state: IncidentState) -> RemediationOption | None:
    if not state.chosen_remediation_id:
        return None
    for opt in state.remediation_options:
        if opt.option_id == state.chosen_remediation_id:
            return opt
    return None


def build_execution_plan(state: IncidentState) -> ExecutionPlan:
    """FixPlan / RemediationOption → ExecutionPlan (intent only)."""
    if state.fix_plan is None:
        raise ValueError("state.fix_plan is required before build_execution_plan()")

    fix_plan = state.fix_plan
    action = _resolve_action(state, fix_plan)
    target = _resolve_target(state, fix_plan)
    option = _chosen_option(state)

    preconditions = list(_PRECONDITIONS_BASE)
    if option is not None and option.rollback_possible:
        preconditions.append("rollback available")
    if fix_plan.risk.value == "high":
        preconditions.append("high-risk change acknowledged")

    expected = list(_EXPECTED_BY_ACTION.get(action, ("pod becomes healthy",)))
    rollback = _ROLLBACK_BY_ACTION.get(action, "manual_rollback")

    return ExecutionPlan(
        action=action,
        target=target,
        preconditions=preconditions,
        expected_outcome=expected,
        rollback_action=rollback,
        hypothesis_id=fix_plan.hypothesis_id,
        remediation_option_id=fix_plan.remediation_option_id,
    )
