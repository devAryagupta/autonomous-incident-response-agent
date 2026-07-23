"""Allowlisted KubectlExecutionProvider + ExecutionPolicy (no live cluster)."""

from __future__ import annotations

import pytest

from incident_agent.calibration.models import CalibratedAssessment
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
from incident_agent.execution.actions.rollout_restart import RolloutRestartAction
from incident_agent.execution.actions.scale_deployment import ScaleDeploymentAction
from incident_agent.execution.actions.base import ActionContext


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
