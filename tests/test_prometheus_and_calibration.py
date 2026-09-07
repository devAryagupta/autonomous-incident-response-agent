"""Prometheus telemetry provider + confidence calibration safety gate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from incident_agent.calibration import (
    MUTATION_SAFETY_THRESHOLD,
    CalibratedAssessment,
    ConfidenceCalibrator,
    PredictionOutcome,
    blend_confidence,
    empirical_bin_accuracy,
    execution_allowed,
    raw_would_allow_execution,
)
from incident_agent.contracts import (
    Alert,
    IncidentState,
    Observations,
)
from incident_agent.contracts import (
    EvidenceRequest as ContractEvidenceRequest,
)
from incident_agent.evidence.models import EvidenceRequest, EvidenceType
from incident_agent.providers.prometheus import (
    BasePrometheusClient,
    FakePrometheusClient,
    PrometheusClient,
    PrometheusMetricsProvider,
    _samples_from_prometheus_payload,
    build_promql,
    normalize_prometheus_url,
)


def _provider(
    series: dict[str, list[float]] | None = None,
) -> PrometheusMetricsProvider:
    client = FakePrometheusClient(
        series
        or {"container_memory_usage_bytes": [300.0, 450.0, 700.0, 900.0]}
    )
    return PrometheusMetricsProvider(client)


def test_fake_client_is_base_prometheus_client() -> None:
    assert isinstance(FakePrometheusClient(), BasePrometheusClient)
    assert issubclass(PrometheusClient, BasePrometheusClient)


def test_fake_telemetry_maps_evidence_request_to_timeseries() -> None:
    provider = _provider()
    req = EvidenceRequest(
        type=EvidenceType.METRIC,
        query="container_memory_usage_bytes",
        target="deployment/payment-service",
        time_range="30m",
    )
    telemetry = provider.query_telemetry(req)
    assert telemetry.datapoints == [300.0, 450.0, 700.0, 900.0]
    assert telemetry.timestamps is not None
    assert len(telemetry.timestamps) == 4


def test_normalize_prometheus_url_strips_ui_paths() -> None:
    assert normalize_prometheus_url("http://localhost:9090/query") == "http://localhost:9090"
    assert normalize_prometheus_url("http://localhost:9090/graph/") == "http://localhost:9090"
    assert normalize_prometheus_url("http://localhost:9090") == "http://localhost:9090"


def test_promql_formatting_for_pod_target() -> None:
    assert build_promql("container_memory_usage_bytes", "pod/oom-demo") == (
        'container_memory_usage_bytes{pod="oom-demo"}'
    )
    assert build_promql(
        "container_memory_usage_bytes",
        "pod/oom-demo",
        namespace="default",
    ) == 'container_memory_usage_bytes{namespace="default",pod="oom-demo"}'


def test_promql_working_set_uses_max_over_time() -> None:
    assert build_promql(
        "container_memory_working_set_bytes",
        "pod/oom-demo",
        namespace="default",
    ) == (
        "max_over_time(container_memory_working_set_bytes"
        '{namespace="default",pod="oom-demo"}[5m])'
    )


def test_promql_formatting_for_deployment_target() -> None:
    promql = build_promql(
        "container_memory_usage_bytes",
        "deployment/payment-service",
    )
    assert promql == 'container_memory_usage_bytes{pod=~"payment-service-.*"}'
    assert "payment-service" in promql
    # Reasoning interfaces unchanged: only string translation here.
    assert build_promql("http_requests_per_second", "deployment/payment-service") == (
        'rate(http_requests_total{service="payment-service"}[5m])'
    )


def test_provider_execute_evidence_request_contract_shape() -> None:
    provider = _provider()
    state = IncidentState(
        incident_id="inc-prom-cal-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(extra={"target_ref": "deployment/payment-service"}),
    )
    result = provider.execute_evidence_request(
        ContractEvidenceRequest(
            request_id="er-1",
            type="metric",
            query="container_memory_usage_bytes",
            target="deployment/payment-service",
        ),
        state=state,
    )
    assert result.success
    assert result.data["series"] == [300.0, 450.0, 700.0, 900.0]
    assert result.data["promql"] == (
        'container_memory_usage_bytes{pod=~"payment-service-.*"}'
    )
    assert "Memory increased from 300Mi to 900Mi" in result.summary


def test_summarize_uses_peak_not_last_sample() -> None:
    provider = _provider({"container_memory_usage_bytes": [10.0, 40.0, 5.0]})
    state = IncidentState(
        incident_id="inc-prom-peak",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(extra={"target_ref": "pod/oom-demo"}),
    )
    result = provider.execute_evidence_request(
        ContractEvidenceRequest(
            request_id="er-peak",
            type="metric",
            query="container_memory_usage_bytes",
            target="pod/oom-demo",
        ),
        state=state,
    )
    assert result.summary == "Memory increased from 10Mi to 40Mi"
    assert result.data["memory_mi"]["peak"] == 40.0


def test_summarize_rising_cycles_is_leak_shaped() -> None:
    provider = _provider(
        {
            "container_memory_usage_bytes": [
                20.0,
                45.0,
                70.0,
                5.0,
                30.0,
                60.0,
                85.0,
                8.0,
                40.0,
                75.0,
                105.0,
            ]
        }
    )
    state = IncidentState(
        incident_id="inc-prom-cycles",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(extra={"target_ref": "pod/oom-demo"}),
    )
    result = provider.execute_evidence_request(
        ContractEvidenceRequest(
            request_id="er-cycles",
            type="metric",
            query="container_memory_usage_bytes",
            target="pod/oom-demo",
        ),
        state=state,
    )
    assert result.summary.startswith("Cycle peaks rose")
    assert "70Mi" in result.summary
    assert "105Mi" in result.summary


def test_summarize_similar_sawtooth_is_startup_allocation() -> None:
    provider = _provider(
        {"container_memory_usage_bytes": [0.2, 71.0, 0.4, 70.0, 0.3, 72.0]}
    )
    state = IncidentState(
        incident_id="inc-prom-saw",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(extra={"target_ref": "pod/oom-demo"}),
    )
    result = provider.execute_evidence_request(
        ContractEvidenceRequest(
            request_id="er-saw",
            type="metric",
            query="container_memory_usage_bytes",
            target="pod/oom-demo",
        ),
        state=state,
    )
    assert result.summary.startswith("Repeated startup allocation")


def test_bytes_series_converted_to_mebibytes() -> None:
    mib = 1024 * 1024
    provider = _provider(
        {"container_memory_usage_bytes": [10.0 * mib, 38.0 * mib]}
    )
    state = IncidentState(
        incident_id="inc-prom-bytes",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(extra={"target_ref": "pod/oom-demo"}),
    )
    result = provider.execute_evidence_request(
        ContractEvidenceRequest(
            request_id="er-bytes",
            type="metric",
            query="container_memory_usage_bytes",
            target="pod/oom-demo",
        ),
        state=state,
    )
    assert result.data["series"] == [10.0, 38.0]
    assert result.summary == "Memory increased from 10Mi to 38Mi"


def test_working_set_compares_peak_to_limit() -> None:
    provider = _provider({"container_memory_working_set_bytes": [20.0, 38.0]})
    state = IncidentState(
        incident_id="inc-prom-ws",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            extra={"target_ref": "pod/oom-demo", "memory_limit": "128Mi"}
        ),
    )
    result = provider.execute_evidence_request(
        ContractEvidenceRequest(
            request_id="er-ws",
            type="metric",
            query="container_memory_working_set_bytes",
            target="pod/oom-demo",
        ),
        state=state,
    )
    assert "Peak RSS 38Mi under limit 128Mi" in result.summary


def test_payload_picks_highest_peak_and_skips_pause() -> None:
    payload = {
        "data": {
            "result": [
                {
                    "metric": {
                        "image": "registry.k8s.io/pause:3.9",
                        "container": "POD",
                    },
                    "values": [[1, "80000000"], [2, "90000000"]],
                },
                {
                    "metric": {"image": "busybox:1.36", "container": "app"},
                    "values": [[1, "10000000"], [2, "40000000"]],
                },
            ]
        }
    }
    samples = _samples_from_prometheus_payload(payload)
    assert [row["value"] for row in samples] == [10000000.0, 40000000.0]


def test_payload_prefers_moving_series_over_flat_leftover() -> None:
    payload = {
        "data": {
            "result": [
                {
                    "metric": {"image": "busybox:1.36", "container": "app"},
                    "values": [[1, "100000000"], [2, "100000000"]],
                },
                {
                    "metric": {"pod": "oom-demo"},
                    "values": [[1, "200000"], [2, "70000000"]],
                },
            ]
        }
    }
    samples = _samples_from_prometheus_payload(payload)
    assert [row["value"] for row in samples] == [200000.0, 70000000.0]


def test_overconfidence_bounding_gates_execution() -> None:
    """
    High raw posterior (0.95) + weak evidence (0.50) + mediocre history (0.75)
    → calibrated score below mutation threshold → execution_gated.
    """
    predicted = 0.95
    verification = 0.50
    historical = 0.75
    expected_blend = blend_confidence(predicted, verification, historical)
    assert expected_blend == pytest.approx(0.95 * 0.4 + 0.50 * 0.4 + 0.75 * 0.2)
    assert expected_blend == pytest.approx(0.73)

    calibrator = ConfidenceCalibrator(safety_threshold=MUTATION_SAFETY_THRESHOLD)
    assessment = calibrator.calibrate(
        predicted_confidence=predicted,
        verification_strength=verification,
        historical_accuracy=historical,
    )
    assert isinstance(assessment, CalibratedAssessment)
    assert assessment.raw_confidence == pytest.approx(0.95)
    assert assessment.calibrated_confidence == pytest.approx(0.73)
    assert assessment.calibrated_confidence < MUTATION_SAFETY_THRESHOLD
    assert assessment.execution_gated is True
    assert "execution_gated=true" in assessment.risk_explanation


def test_safety_gate_blocks_where_raw_confidence_would_allow() -> None:
    """Uncalibrated 0.95 would fire; calibrated engine blocks mutation."""
    threshold = MUTATION_SAFETY_THRESHOLD
    raw = 0.95
    assert raw_would_allow_execution(raw, safety_threshold=threshold) is True

    assessment = ConfidenceCalibrator(safety_threshold=threshold).calibrate(
        predicted_confidence=raw,
        verification_strength=0.50,
        historical_accuracy=0.75,
    )
    assert assessment.execution_gated is True
    assert execution_allowed(assessment) is False
    assert assessment.calibrated_confidence < threshold <= raw


def test_empirical_bin_accuracy_half_open_and_includes_one() -> None:
    history = [
        PredictionOutcome(predicted_confidence=0.49, resolved=True),
        PredictionOutcome(predicted_confidence=0.50, resolved=False),
        PredictionOutcome(predicted_confidence=1.0, resolved=True),
        PredictionOutcome(predicted_confidence=0.95, resolved=False),
    ]
    low = empirical_bin_accuracy(history, lo=0.0, hi=0.5)
    assert low == pytest.approx(1.0)
    mid = empirical_bin_accuracy(history, lo=0.5, hi=0.6)
    assert mid == pytest.approx(0.0)
    high = empirical_bin_accuracy(history, lo=0.9, hi=1.0)
    assert high == pytest.approx(0.5)
    assert empirical_bin_accuracy(history, lo=0.7, hi=0.8) is None


def test_bin_scaling_bounds_overconfident_bin() -> None:
    """
    History: predictions in [0.9, 1.0] only succeed 85% → scale 0.95 toward 0.85.
    """
    history = [
        PredictionOutcome(predicted_confidence=0.92, resolved=True),
        PredictionOutcome(predicted_confidence=0.94, resolved=True),
        PredictionOutcome(predicted_confidence=0.96, resolved=False),
        PredictionOutcome(predicted_confidence=0.98, resolved=True),
        PredictionOutcome(predicted_confidence=0.91, resolved=True),
        PredictionOutcome(predicted_confidence=0.93, resolved=True),
        PredictionOutcome(predicted_confidence=0.97, resolved=True),
        PredictionOutcome(predicted_confidence=0.99, resolved=False),
        PredictionOutcome(predicted_confidence=0.95, resolved=True),
        PredictionOutcome(predicted_confidence=0.90, resolved=True),
        PredictionOutcome(predicted_confidence=0.91, resolved=False),
        PredictionOutcome(predicted_confidence=0.92, resolved=True),
        PredictionOutcome(predicted_confidence=0.93, resolved=True),
        PredictionOutcome(predicted_confidence=0.94, resolved=True),
        PredictionOutcome(predicted_confidence=0.95, resolved=False),
        PredictionOutcome(predicted_confidence=0.96, resolved=True),
        PredictionOutcome(predicted_confidence=0.97, resolved=True),
        PredictionOutcome(predicted_confidence=0.98, resolved=True),
        PredictionOutcome(predicted_confidence=0.99, resolved=True),
        PredictionOutcome(predicted_confidence=0.95, resolved=False),
    ]
    # 15/20 = 0.75 success in high bin for this fixture — use controlled 85%.
    history_85 = (
        [PredictionOutcome(predicted_confidence=0.95, resolved=True) for _ in range(17)]
        + [PredictionOutcome(predicted_confidence=0.95, resolved=False) for _ in range(3)]
    )
    assert sum(1 for h in history_85 if h.resolved) / len(history_85) == pytest.approx(0.85)

    # Blend alone with strong inputs stays high; bin scaling pulls it down.
    calibrator = ConfidenceCalibrator(
        safety_threshold=0.80,
        history=history_85,
    )
    assessment = calibrator.calibrate(
        predicted_confidence=0.95,
        verification_strength=0.95,
        historical_accuracy=0.95,
    )
    # blend = 0.95; bin accuracy 0.85 → calibrated min(0.95, 0.85) = 0.85
    assert assessment.bin_adjusted is True
    assert assessment.calibrated_confidence == pytest.approx(0.85)
    _ = history  # retained for readability of alternate fixture


def test_strong_evidence_can_clear_mutation_gate() -> None:
    assessment = ConfidenceCalibrator(safety_threshold=MUTATION_SAFETY_THRESHOLD).calibrate(
        predicted_confidence=0.92,
        verification_strength=0.90,
        historical_accuracy=0.88,
    )
    # 0.92*0.4 + 0.90*0.4 + 0.88*0.2 = 0.368 + 0.36 + 0.176 = 0.904
    assert assessment.calibrated_confidence == pytest.approx(0.904)
    assert assessment.execution_gated is False
    assert execution_allowed(assessment) is True
