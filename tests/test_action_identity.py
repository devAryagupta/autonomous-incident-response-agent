"""Decision action == execution action == recorded action. No silent rewrite."""

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    ExecutionPlan,
    FixAction,
    FixActionType,
    FixPlan,
    IncidentState,
    Observations,
    RemediationOption,
    RiskLevel,
)
from incident_agent.execution import (
    FakeKubectlClient,
    KubectlExecutionProvider,
    decision_action,
)
from incident_agent.execution.plan import build_execution_plan

# kubectl verb that must run for each decision identity.
_CLIENT_METHOD = {
    "restart_pod": "delete_pod",
    "rollback_deployment": "rollout_undo_deployment",
    "rollout_restart": "rollout_restart_deployment",
    "scale_deployment": "scale_deployment",
    "increase_memory_limit": "update_container_memory_limit",
    "update_resource_limit": "update_container_memory_limit",
}

_KIND = {
    "restart_pod": FixActionType.RESTART_POD,
    "rollback_deployment": FixActionType.ROLLBACK_DEPLOYMENT,
    "rollout_restart": FixActionType.ROLLOUT_RESTART,
    "scale_deployment": FixActionType.SCALE_DEPLOYMENT,
    "increase_memory_limit": FixActionType.PATCH_RESOURCE,
    "update_resource_limit": FixActionType.PATCH_RESOURCE,
}


def _state_for(action: str) -> IncidentState:
    target = (
        "pod/payment-service-7d9f8"
        if action == "restart_pod"
        else "deployment/payment-service"
    )
    params: dict = {"action": action, "change": action}
    if action == "scale_deployment":
        params["desired_replicas"] = 3
    if action in {"increase_memory_limit", "update_resource_limit"}:
        params["container_name"] = "app"
        params["memory_limit"] = "512Mi"
    option_id = f"r1-{action}"
    state = IncidentState(
        incident_id=f"inc-id-{action}",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["CrashLoopBackOff"],
            extra={
                "target_ref": target,
                "namespace": "default",
                "kubectl_dry_run": True,
                "safety_threshold": 0.0,
            },
        ),
        confidence_score=0.99,
        remediation_options=[
            RemediationOption(
                option_id=option_id,
                action=action,
                expected_effect="test",
                risk=RiskLevel.LOW,
                rationale="identity test",
            )
        ],
        chosen_remediation_id=option_id,
        fix_plan=FixPlan(
            hypothesis_id="h1",
            remediation_option_id=option_id,
            risk=RiskLevel.LOW,
            actions=[
                FixAction(
                    action_type=_KIND[action],
                    target=target,
                    params=params,
                    rationale="identity test",
                )
            ],
        ),
    )
    state.execution_plan = ExecutionPlan(
        action=action,
        target=target,
        preconditions=["test"],
        expected_outcome=["test"],
        remediation_option_id=option_id,
    )
    return state


def _client() -> FakeKubectlClient:
    return FakeKubectlClient(
        pods={("default", "payment-service-7d9f8")},
        deployments={
            ("default", "payment-service"): {
                "replicas": 2,
                "containers": {"app": {"memory_limit": "256Mi"}},
            }
        },
    )


def test_decision_execution_recorded_identity_matches_for_every_action() -> None:
    for action, method in _CLIENT_METHOD.items():
        state = _state_for(action)
        decided = decision_action(state)
        prepared = build_execution_plan(state).action
        client = _client()
        result = KubectlExecutionProvider(client, default_dry_run=True).execute(
            state.fix_plan,  # type: ignore[arg-type]
            state=state,
        )
        assert decided == action
        assert prepared == action
        assert result.action == action
        assert decided == prepared == result.action
        assert any(m.method == method for m in client.mutations), (
            f"{action} must call {method}, got {[m.method for m in client.mutations]}"
        )


def test_rollback_is_not_a_restart() -> None:
    state = _state_for("rollback_deployment")
    client = _client()
    result = KubectlExecutionProvider(client, default_dry_run=True).execute(
        state.fix_plan,  # type: ignore[arg-type]
        state=state,
    )
    assert result.action == "rollback_deployment"
    assert any(m.method == "rollout_undo_deployment" for m in client.mutations)
    assert not any(m.method == "rollout_restart_deployment" for m in client.mutations)
    assert not any(m.method == "delete_pod" for m in client.mutations)


def test_rollout_restart_is_not_a_rollback() -> None:
    state = _state_for("rollout_restart")
    client = _client()
    result = KubectlExecutionProvider(client, default_dry_run=True).execute(
        state.fix_plan,  # type: ignore[arg-type]
        state=state,
    )
    assert result.action == "rollout_restart"
    assert any(m.method == "rollout_restart_deployment" for m in client.mutations)
    assert not any(m.method == "rollout_undo_deployment" for m in client.mutations)


def test_restart_pod_is_not_a_rollout() -> None:
    state = _state_for("restart_pod")
    client = _client()
    result = KubectlExecutionProvider(client, default_dry_run=True).execute(
        state.fix_plan,  # type: ignore[arg-type]
        state=state,
    )
    assert result.action == "restart_pod"
    assert any(m.method == "delete_pod" for m in client.mutations)
    assert not any(m.method.startswith("rollout_") for m in client.mutations)
