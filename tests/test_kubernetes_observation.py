"""KubernetesObservationProvider: fake client → Observations → existing diagnosis."""

from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    EvidenceRequest,
    IncidentState,
    Observations,
    ResourceRef,
)
from incident_agent.diagnosis.engine import CATEGORY_OOMKILLED, diagnose_observations
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import (
    ContainerStatusSnapshot,
    EventRecord,
    KubernetesObservationProvider,
    ObservationProvider,
    PodSnapshot,
    k8s_observation_providers,
)


class FakeKubernetesClient:
    """In-memory cluster stand-in — no kubeconfig / network."""

    def __init__(
        self,
        *,
        pods: dict[tuple[str, str], PodSnapshot] | None = None,
        events: dict[tuple[str, str], list[EventRecord]] | None = None,
        logs: dict[tuple[str, str], list[str]] | None = None,
        previous_logs: dict[tuple[str, str], list[str]] | None = None,
        secrets: set[tuple[str, str]] | None = None,
        workload_pods: dict[tuple[str, str, str], str] | None = None,
    ) -> None:
        self.pods = pods or {}
        self.events = events or {}
        self.logs = logs or {}
        self.previous_logs = previous_logs or {}
        self.secrets = secrets or set()
        self.workload_pods = workload_pods or {}

    def get_pod(self, namespace: str, name: str) -> PodSnapshot | None:
        return self.pods.get((namespace, name))

    def find_pod_for_workload(
        self,
        namespace: str,
        *,
        kind: str | None,
        name: str,
    ) -> PodSnapshot | None:
        key = (namespace, (kind or "deployment").lower(), name)
        pod_name = self.workload_pods.get(key)
        if pod_name is None:
            return self.get_pod(namespace, name)
        return self.get_pod(namespace, pod_name)

    def list_pod_events(self, namespace: str, pod_name: str) -> list[EventRecord]:
        return list(self.events.get((namespace, pod_name), []))

    def read_container_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 50,
    ) -> list[str]:
        _ = container
        source = self.previous_logs if previous else self.logs
        return list(source.get((namespace, pod_name), []))[:tail_lines]

    def secret_exists(self, namespace: str, secret_name: str) -> bool:
        return (namespace, secret_name) in self.secrets


def _oom_pod() -> PodSnapshot:
    return PodSnapshot(
        namespace="payments",
        name="payment-service-7d9f8",
        phase="Running",
        ready=False,
        restart_count=5,
        image="payment:v1",
        exit_code=137,
        terminated_reason="OOMKilled",
        waiting_reason="CrashLoopBackOff",
        memory_request="128Mi",
        memory_limit="256Mi",
        containers=(
            ContainerStatusSnapshot(
                name="app",
                image="payment:v1",
                ready=False,
                restart_count=5,
                state="waiting",
                reason="CrashLoopBackOff",
                exit_code=137,
            ),
        ),
        secret_refs=("db-credentials",),
        labels={"app": "payment-service"},
    )


def _state_for_pod(*, use_resource: bool = True) -> IncidentState:
    resource = (
        ResourceRef(
            system="kubernetes",
            namespace="payments",
            kind="Pod",
            name="payment-service-7d9f8",
        )
        if use_resource
        else None
    )
    return IncidentState(
        incident_id="inc-k8s-oom-1",
        created_at=datetime.now(tz=UTC),
        resource=resource,
        alert=Alert(
            alert_name="KubePodCrashLooping",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
            labels={"namespace": "payments", "pod": "payment-service-7d9f8"},
        ),
        observations=Observations(
            extra={"target_ref": "pod/payment-service-7d9f8", "top_n": 3},
        ),
    )


def _fake_oom_client() -> FakeKubernetesClient:
    pod = _oom_pod()
    return FakeKubernetesClient(
        pods={("payments", pod.name): pod},
        events={
            ("payments", pod.name): [
                EventRecord(
                    type="Warning",
                    reason="OOMKilled",
                    message="Container app was OOMKilled",
                    count=3,
                ),
                EventRecord(
                    type="Warning",
                    reason="BackOff",
                    message="Back-off restarting failed container app",
                    count=5,
                ),
            ]
        },
        logs={
            ("payments", pod.name): [
                "FATAL: worker heap exhausted",
            ]
        },
        previous_logs={
            ("payments", pod.name): [
                "Traceback (most recent call last):",
                "MemoryError: unable to allocate",
            ]
        },
        secrets={("payments", "db-credentials")},
    )


def test_kubernetes_provider_implements_protocol() -> None:
    provider = KubernetesObservationProvider(_fake_oom_client())
    assert isinstance(provider, ObservationProvider)


def test_fetch_maps_cluster_to_observations_contract() -> None:
    provider = KubernetesObservationProvider(_fake_oom_client())
    obs = provider.fetch_observations(_state_for_pod())

    assert any("exited with code 137" in line.lower() for line in obs.logs)
    assert any("OOMKilled" in line for line in obs.events)
    assert obs.extra["restart_count"] == 5
    assert obs.extra["image"] == "payment:v1"
    assert obs.extra["provider"] == "kubernetes"
    assert obs.extra["target_ref"] == "pod/payment-service-7d9f8"


def test_mapped_observations_drive_existing_diagnosis() -> None:
    provider = KubernetesObservationProvider(_fake_oom_client())
    obs = provider.fetch_observations(_state_for_pod())
    diagnosis = diagnose_observations(obs)
    assert diagnosis.category == CATEGORY_OOMKILLED
    assert diagnosis.confidence >= 0.85


def test_restart_history_and_pod_health_evidence() -> None:
    provider = KubernetesObservationProvider(_fake_oom_client())
    state = _state_for_pod()
    state.observations = provider.fetch_observations(state)

    restart = provider.execute_evidence_request(
        EvidenceRequest(
            request_id="er-1",
            type="event",
            query="restart_history",
            target="pod/payment-service-7d9f8",
        ),
        state=state,
    )
    assert restart.success
    assert restart.data["restart_count"] == 5
    assert "restart_count=5" in restart.summary

    health = provider.execute_evidence_request(
        EvidenceRequest(
            request_id="er-2",
            type="describe",
            query="pod_health",
            target="pod/payment-service-7d9f8",
        ),
        state=state,
    )
    assert health.success
    assert health.data["ready"] is False
    assert "CrashLoopBackOff" in health.summary


def test_healthy_pod_summaries_match_outcome_patterns() -> None:
    healthy = PodSnapshot(
        namespace="payments",
        name="payment-service-7d9f8",
        phase="Running",
        ready=True,
        restart_count=0,
        image="payment:v2",
        containers=(
            ContainerStatusSnapshot(
                name="app",
                image="payment:v2",
                ready=True,
                restart_count=0,
                state="running",
            ),
        ),
        secret_refs=("db-credentials",),
    )
    client = FakeKubernetesClient(
        pods={("payments", healthy.name): healthy},
        secrets={("payments", "db-credentials")},
    )
    provider = KubernetesObservationProvider(client)
    state = _state_for_pod()
    state.observations = provider.fetch_observations(state)

    restart = provider.execute_evidence_request(
        EvidenceRequest(
            request_id="er-1",
            type="event",
            query="restart_history",
            target="pod/payment-service-7d9f8",
        ),
        state=state,
    )
    assert "restart_count=0" in restart.summary
    assert "no recent restarts" in restart.summary

    health = provider.execute_evidence_request(
        EvidenceRequest(
            request_id="er-2",
            type="describe",
            query="pod_health",
            target="pod/payment-service-7d9f8",
        ),
        state=state,
    )
    assert "Ready" in health.summary
    assert "pod becomes healthy" in health.summary
    assert "crashloop clears" in health.summary.lower()


def test_k8s_bundle_enrich_leaves_reasoning_untouched() -> None:
    bundle = k8s_observation_providers(client=_fake_oom_client())
    assert isinstance(bundle.observations, KubernetesObservationProvider)
    updates = enrich(_state_for_pod(), providers=bundle)
    obs = updates["observations"]
    assert isinstance(obs, Observations)
    assert obs.extra["observation_provider"] == "KubernetesObservationProvider"
    assert obs.extra["metrics_provider"] == "SyntheticMetricsProvider"
    assert any("OOMKilled" in line for line in obs.events)


def test_resolve_from_target_ref_without_resource() -> None:
    pod = _oom_pod()
    client = FakeKubernetesClient(pods={("payments", pod.name): pod})
    provider = KubernetesObservationProvider(client)
    state = _state_for_pod(use_resource=False)
    state.alert.labels = {"namespace": "payments"}
    state.observations.extra["target_ref"] = "pod/payment-service-7d9f8"
    obs = provider.fetch_observations(state)
    assert obs.extra.get("error") is None
    assert obs.extra["pod"] == pod.name


def test_missing_pod_reports_error_without_raising() -> None:
    provider = KubernetesObservationProvider(FakeKubernetesClient())
    obs = provider.fetch_observations(_state_for_pod())
    assert obs.extra["error"] == "pod_not_found"
    assert obs.extra["provider"] == "kubernetes"
