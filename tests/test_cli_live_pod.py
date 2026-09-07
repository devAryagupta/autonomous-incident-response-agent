"""Live-pod CLI is an entry point: build state, invoke GRAPH, print."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO

from incident_agent.cli.live_pod import (
    DEFAULT_PROMETHEUS_URL,
    _parse_args,
    incident_state_for_pod,
    print_run_report,
    run_live_pod,
)
from incident_agent.diagnosis.engine import CATEGORY_OOMKILLED
from incident_agent.graph import GRAPH
from incident_agent.providers import (
    FakePrometheusClient,
    PrometheusMetricsProvider,
    SyntheticMetricsProvider,
    k8s_observation_providers,
)
from incident_agent.verification.conclusion import summarize_cause_resolution
from tests.test_kubernetes_observation import _fake_oom_client, _state_for_pod


def test_incident_state_names_oom_demo() -> None:
    state = incident_state_for_pod(namespace="default", name="oom-demo")
    assert state.incident_id == "live-default-oom-demo"
    assert state.resource is not None
    assert state.resource.kind == "Pod"
    assert state.resource.name == "oom-demo"
    assert state.observations.extra["target_ref"] == "pod/oom-demo"
    assert state.alert.labels == {"namespace": "default", "pod": "oom-demo"}
    assert state.observations.logs == []
    assert state.observations.events == []


def test_run_live_pod_uses_injected_providers() -> None:
    bundle = k8s_observation_providers(client=_fake_oom_client())
    out = run_live_pod(
        namespace="payments",
        pod="payment-service-7d9f8",
        providers=bundle,
    )
    assert out.observations.extra["observation_provider"] == "KubernetesObservationProvider"
    assert out.diagnosis is not None
    assert out.diagnosis.category == CATEGORY_OOMKILLED
    assert out.chosen_hypothesis_id is None
    assert all(item.result == "inconclusive" for item in out.hypothesis_verifications)
    assert summarize_cause_resolution(out) == (
        "OOM confirmed, root cause not yet distinguishable"
    )


def test_print_run_report_has_required_sections() -> None:
    bundle = k8s_observation_providers(client=_fake_oom_client())
    out = run_live_pod(
        namespace="payments",
        pod="payment-service-7d9f8",
        providers=bundle,
    )
    buffer = StringIO()
    with redirect_stdout(buffer):
        print_run_report(out)
    text = buffer.getvalue()
    for heading in (
        "Diagnosis:",
        "Evidence:",
        "Hypotheses:",
        "Conclusion:",
        "Remediation:",
        "Execution:",
    ):
        assert heading in text
    assert CATEGORY_OOMKILLED in text
    assert "inconclusive" in text
    assert "OOM confirmed, root cause not yet distinguishable" in text
    assert "Metrics: SyntheticMetricsProvider" in text


def test_parse_args_prometheus_flags() -> None:
    defaulted = _parse_args([])
    assert defaulted.prometheus_url == DEFAULT_PROMETHEUS_URL
    assert defaulted.no_prometheus is False
    skipped = _parse_args(["--no-prometheus"])
    assert skipped.no_prometheus is True
    custom = _parse_args(["--prometheus-url", "http://localhost:9090/query"])
    assert custom.prometheus_url == "http://localhost:9090/query"


def test_k8s_bundle_uses_prometheus_when_metrics_client_given() -> None:
    bundle = k8s_observation_providers(
        client=_fake_oom_client(),
        metrics_client=FakePrometheusClient(
            {"container_memory_usage_bytes": [200.0, 450.0, 900.0]}
        ),
    )
    assert isinstance(bundle.metrics, PrometheusMetricsProvider)
    bare = k8s_observation_providers(client=_fake_oom_client())
    assert isinstance(bare.metrics, SyntheticMetricsProvider)


def test_k8s_plus_prometheus_growth_confirms_leak() -> None:
    bundle = k8s_observation_providers(
        client=_fake_oom_client(),
        metrics_client=FakePrometheusClient(
            {
                "container_memory_usage_bytes": [200.0, 450.0, 900.0],
                "container_memory_working_set_bytes": [180.0, 200.0],
                "http_requests_per_second": [10.0, 11.0, 10.0],
            }
        ),
    )
    out = GRAPH.invoke(_state_for_pod(), providers=bundle)
    results = {item.hypothesis: item.result for item in out.hypothesis_verifications}
    assert results["Memory leak"] == "confirmed"
    assert results["Memory limit too low"] == "inconclusive"
    assert out.chosen_hypothesis_id is not None
