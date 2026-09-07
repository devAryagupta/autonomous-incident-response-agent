"""Allowlisted KubectlExecutionProvider + ExecutionPolicy (no live cluster)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from incident_agent.calibration.models import CalibratedAssessment
from incident_agent.contracts import (
    Alert,
    FixAction,
    FixActionType,
    FixPlan,
    IncidentState,
    Observations,
)
from incident_agent.contracts import RiskLevel as ContractRisk
from incident_agent.execution import (
    ActionRegistry,
    ActionRequest,
    ActionStatus,
    ExecutionPolicy,
    FakeKubectlClient,
    KubectlExecutionProvider,
    RiskLevel,
    UnknownActionError,
)
from incident_agent.execution.actions.base import ActionContext
from incident_agent.execution.actions.rollout_restart import RolloutRestartAction
from incident_agent.execution.actions.scale_deployment import ScaleDeploymentAction


def _assessment(
    calibrated: float,
    *,
    gated: bool = False,
) -> CalibratedAssessment:
    return CalibratedAssessment(
        raw_confidence=calibrated,
        calibrated_confidence=calibrated,
        execution_gated=gated,
        risk_explanation="test",
        safety_threshold=0.80,
    )


def test_allowlist_rejects_unknown_action_type() -> None:
    provider = KubectlExecutionProvider(FakeKubectlClient())
    with pytest.raises(UnknownActionError, match="not allowlisted"):
        provider.execute_action(
            ActionRequest(
                type="raw_command",
                target="payment-service",
                namespace="production",
                parameters={"cmd": "kubectl delete ns kube-system"},
            ),
            calibrated_assessment=_assessment(0.99),
        )


def test_allowlist_rejects_registering_unknown_handler() -> None:
    client = FakeKubectlClient()
    registry = ActionRegistry.default(client)

    class _Evil:
        action_type = "raw_command"
        risk_level = RiskLevel.CRITICAL

        def validate(self, context: ActionContext) -> bool:
            return True

        def execute(self, context: ActionContext, *, dry_run: bool = False):
            raise AssertionError("must not run")

        def rollback(self, context: ActionContext):
            raise AssertionError("must not run")

    with pytest.raises(UnknownActionError, match="non-allowlisted"):
        registry.register(_Evil())  # type: ignore[arg-type]


def test_policy_threshold_gate_medium_risk() -> None:
    client = FakeKubectlClient(
        deployments={("production", "payment-service"): {"replicas": 2, "containers": {}}}
    )
    handler = RolloutRestartAction(client)
    policy = ExecutionPolicy()
    context = ActionContext(target="payment-service", namespace="production")

    denied = policy.evaluate(
        calibrated_assessment=_assessment(0.80),
        action_handler=handler,
        action_context=context,
    )
    assert denied.allowed is False
    assert denied.requires_human_approval is True
    assert denied.required_confidence == 0.85

    allowed = policy.evaluate(
        calibrated_assessment=_assessment(0.88),
        action_handler=handler,
        action_context=context,
    )
    assert allowed.allowed is True
    assert allowed.requires_human_approval is False


def test_provider_blocks_medium_action_below_threshold_and_queues_approval() -> None:
    client = FakeKubectlClient(
        deployments={("production", "payment-service"): {"replicas": 2, "containers": {}}}
    )
    provider = KubectlExecutionProvider(client, default_dry_run=True)
    result = provider.execute_action(
        ActionRequest(
            type="rollout_restart",
            target="payment-service",
            namespace="production",
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.80),
    )
    assert result.status == ActionStatus.BLOCKED_BY_POLICY
    assert result.requires_human_approval is True
    assert len(provider.approval_queue) == 1
    assert not any(m.method == "rollout_restart_deployment" for m in client.mutations)


def test_dry_run_restart_pod_records_but_does_not_mutate() -> None:
    client = FakeKubectlClient(pods={("default", "payment-service-7d9f8")})
    provider = KubectlExecutionProvider(client)
    before = set(client.pods)

    result = provider.execute_action(
        ActionRequest(
            type="restart_pod",
            target="payment-service-7d9f8",
            namespace="default",
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.90),
    )
    assert result.status == ActionStatus.DRY_RUN
    assert result.success is True
    assert result.dry_run is True
    assert client.pods == before  # no actual deletion
    assert any(m.method == "delete_pod" and m.dry_run for m in client.mutations)
    assert result.applied_changes == []


def test_live_mutation_path_deletes_pod_when_not_dry_run() -> None:
    client = FakeKubectlClient(pods={("default", "payment-service-7d9f8")})
    provider = KubectlExecutionProvider(client, default_dry_run=False)
    result = provider.execute_action(
        ActionRequest(
            type="restart_pod",
            target="payment-service-7d9f8",
            namespace="default",
            dry_run=False,
        ),
        calibrated_assessment=_assessment(0.90),
    )
    assert result.status == ActionStatus.SUCCESS
    assert ("default", "payment-service-7d9f8") not in client.pods


def test_scale_deployment_bounds_validation() -> None:
    client = FakeKubectlClient(
        deployments={("default", "payment-service"): {"replicas": 2, "containers": {}}}
    )
    action = ScaleDeploymentAction(client, max_allowed_replicas=10)

    assert (
        action.validate(
            ActionContext(
                target="payment-service",
                namespace="default",
                parameters={"desired_replicas": 0},
            )
        )
        is False
    )
    assert (
        action.validate(
            ActionContext(
                target="payment-service",
                namespace="default",
                parameters={"desired_replicas": 11},
            )
        )
        is False
    )
    assert (
        action.validate(
            ActionContext(
                target="payment-service",
                namespace="default",
                parameters={"desired_replicas": 3},
            )
        )
        is True
    )

    provider = KubectlExecutionProvider(client)
    failed = provider.execute_action(
        ActionRequest(
            type="scale_deployment",
            target="payment-service",
            parameters={"desired_replicas": 0},
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.99),
    )
    assert failed.status == ActionStatus.VALIDATION_FAILED
    # Validation fails before policy / mutation.
    assert not any(m.method == "scale_deployment" for m in client.mutations)


def test_execution_gated_assessment_forces_human_approval() -> None:
    client = FakeKubectlClient(
        deployments={("default", "payment-service"): {"replicas": 2, "containers": {}}}
    )
    provider = KubectlExecutionProvider(client)
    result = provider.execute_action(
        ActionRequest(
            type="rollout_restart",
            target="payment-service",
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.99, gated=True),
    )
    assert result.status == ActionStatus.BLOCKED_BY_POLICY
    assert result.requires_human_approval is True


def test_missing_memory_limit_is_invalid_not_512mi() -> None:
    client = FakeKubectlClient(
        deployments={
            ("default", "payment-service"): {
                "replicas": 2,
                "containers": {"app": {"memory_limit": "1Gi"}},
            }
        }
    )
    provider = KubectlExecutionProvider(client, default_dry_run=True)
    failed = provider.execute_action(
        ActionRequest(
            type="update_resource_limit",
            target="deployment/payment-service",
            parameters={"container_name": "app"},
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.99),
    )
    assert failed.status == ActionStatus.VALIDATION_FAILED
    assert "memory_limit" in (failed.reason or "")
    assert not any(m.method == "update_container_memory_limit" for m in client.mutations)


def test_execute_plan_without_limit_does_not_invent_512mi() -> None:
    client = FakeKubectlClient(
        deployments={
            ("default", "payment-service"): {
                "replicas": 2,
                "containers": {"app": {"memory_limit": "1Gi"}},
            }
        }
    )
    stamp = datetime.now(tz=UTC)
    state = IncidentState(
        incident_id="inc-no-limit",
        created_at=stamp,
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=stamp),
        observations=Observations(
            extra={
                "target_ref": "deployment/payment-service",
                "namespace": "default",
                "kubectl_dry_run": True,
                "safety_threshold": 0.0,
            }
        ),
        confidence_score=0.99,
        fix_plan=FixPlan(
            hypothesis_id="h1",
            risk=ContractRisk.HIGH,
            actions=[
                FixAction(
                    action_type=FixActionType.PATCH_RESOURCE,
                    target="deployment/payment-service",
                    params={"action": "increase_memory_limit", "change": "raise limit"},
                    rationale="test",
                )
            ],
        ),
    )
    result = KubectlExecutionProvider(client, default_dry_run=True).execute(
        state.fix_plan, state=state
    )
    assert result.success is False
    assert not any(m.method == "update_container_memory_limit" for m in client.mutations)


def test_explicit_new_memory_limit_is_used() -> None:
    client = FakeKubectlClient(
        deployments={
            ("default", "payment-service"): {
                "replicas": 2,
                "containers": {"app": {"memory_limit": "1Gi"}},
            }
        }
    )
    provider = KubectlExecutionProvider(client, default_dry_run=True)
    result = provider.execute_action(
        ActionRequest(
            type="update_resource_limit",
            target="deployment/payment-service",
            parameters={"container_name": "app", "new_memory_limit": "2Gi"},
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.99),
    )
    assert result.status == ActionStatus.DRY_RUN
    assert any(
        m.method == "update_container_memory_limit"
        and m.params.get("memory_limit") == "2Gi"
        for m in client.mutations
    )


def test_provider_resource_exists_delegates_to_client() -> None:
    client = FakeKubectlClient()
    provider = KubectlExecutionProvider(client)
    assert provider.resource_exists("deployment", "default", "payment-service") is True
    assert provider.resource_exists("deployment", "default", "missing") is False
    assert ("deployment", "default", "payment-service") in client.lookups
    assert ("deployment", "default", "missing") in client.lookups


def test_high_risk_update_resource_requires_095() -> None:
    client = FakeKubectlClient(
        deployments={
            ("default", "payment-service"): {
                "replicas": 2,
                "containers": {"app": {"memory_limit": "256Mi"}},
            }
        }
    )
    provider = KubectlExecutionProvider(client)
    blocked = provider.execute_action(
        ActionRequest(
            type="update_resource_limit",
            target="deployment/payment-service",
            parameters={"container_name": "app", "memory_limit": "1Gi"},
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.90),
    )
    assert blocked.status == ActionStatus.BLOCKED_BY_POLICY

    allowed = provider.execute_action(
        ActionRequest(
            type="update_resource_limit",
            target="deployment/payment-service",
            parameters={"container_name": "app", "memory_limit": "1Gi"},
            dry_run=True,
        ),
        calibrated_assessment=_assessment(0.96),
    )
    assert allowed.status == ActionStatus.DRY_RUN
    assert allowed.success is True
