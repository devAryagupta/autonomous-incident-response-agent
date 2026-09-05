"""Outcome verification: synthetic regex vs Kubernetes pod state."""

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    ExecutionPlan,
    ExecutionResult,
    FixAction,
    FixActionType,
    FixPlan,
    IncidentState,
    Observations,
    ResourceRef,
    RiskLevel,
)
from incident_agent.execution.outcome import verify_execution_outcome
from incident_agent.providers import (
    EventRecord,
    PodSnapshot,
    default_providers,
    k8s_observation_providers,
)
from incident_agent.providers.kubernetes import ContainerStatusSnapshot


class _FakeK8s:
    def __init__(self, pod: PodSnapshot) -> None:
        self._pod = pod

    def get_pod(self, namespace: str, name: str) -> PodSnapshot | None:
        if namespace == self._pod.namespace and name == self._pod.name:
            return self._pod
        return None

    def find_pod_for_workload(
        self,
        namespace: str,
        *,
        kind: str | None,
        name: str,
    ) -> PodSnapshot | None:
        _ = kind, name
        return self._pod if namespace == self._pod.namespace else None

    def list_pod_events(self, namespace: str, pod_name: str) -> list[EventRecord]:
        _ = namespace, pod_name
        return []

    def read_container_logs(self, *args: object, **kwargs: object) -> list[str]:
        return []

    def secret_exists(self, namespace: str, secret_name: str) -> bool:
        _ = namespace, secret_name
        return True


def _executed_state(*, logs: list[str]) -> IncidentState:
    stamp = datetime.now(tz=UTC)
    return IncidentState(
        incident_id="inc-outcome-1",
        created_at=stamp,
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=stamp),
        resource=ResourceRef(
            namespace="payments",
            kind="Deployment",
            name="payment-service",
        ),
        observations=Observations(
            logs=logs,
            extra={"target_ref": "deployment/payment-service", "namespace": "payments"},
        ),
        fix_plan=FixPlan(
            hypothesis_id="h1",
            risk=RiskLevel.MEDIUM,
            actions=[
                FixAction(
                    action_type=FixActionType.PATCH_RESOURCE,
                    target="deployment/payment-service",
                    params={"action": "increase_memory_limit"},
                    rationale="test",
                )
            ],
        ),
        execution_plan=ExecutionPlan(
            action="increase_memory_limit",
            target="deployment/payment-service",
            expected_outcome=["pod becomes healthy", "restart count decreases"],
        ),
        execution=ExecutionResult(
            executed=True,
            success=True,
            status="success",
            action="increase_memory_limit",
            applied_changes=["memory limit updated"],
            summary="ok",
            started_at=stamp,
            finished_at=stamp,
        ),
    )


def test_synthetic_outcome_still_uses_regex_summaries() -> None:
    state = _executed_state(logs=["CrashLoopBackOff"])
    outcome, _obs = verify_execution_outcome(state, providers=default_providers())
    assert outcome.resolved is True
    assert "pod becomes healthy" in outcome.observed_outcome
    assert "synthetic" in (outcome.reason or "")


def test_kubernetes_outcome_ignores_healthy_phrase_in_logs() -> None:
    crashing = PodSnapshot(
        namespace="payments",
        name="payment-service-7d9f8",
        phase="Running",
        ready=False,
        restart_count=5,
        waiting_reason="CrashLoopBackOff",
        terminated_reason="OOMKilled",
        containers=(
            ContainerStatusSnapshot(
                name="app",
                ready=False,
                restart_count=5,
                state="waiting",
                reason="CrashLoopBackOff",
            ),
        ),
    )
    state = _executed_state(logs=["Pod is healthy", "Ready"])
    providers = k8s_observation_providers(client=_FakeK8s(crashing))
    outcome, observations = verify_execution_outcome(state, providers=providers)
    assert outcome.resolved is False
    assert "pod becomes healthy" in outcome.unmet_expectations
    assert observations.extra.get("outcome_source") == "kubernetes"
    assert "cluster state" in outcome.reason


def test_kubernetes_outcome_uses_ready_phase_and_restart_count() -> None:
    healthy = PodSnapshot(
        namespace="payments",
        name="payment-service-7d9f8",
        phase="Running",
        ready=True,
        restart_count=0,
        containers=(
            ContainerStatusSnapshot(name="app", ready=True, restart_count=0, state="running"),
        ),
    )
    state = _executed_state(logs=["still CrashLoopBackOff in an old line"])
    providers = k8s_observation_providers(client=_FakeK8s(healthy))
    outcome, _obs = verify_execution_outcome(state, providers=providers)
    assert "pod becomes healthy" in outcome.observed_outcome
    assert "restart count decreases" in outcome.observed_outcome
    assert outcome.resolved is True
