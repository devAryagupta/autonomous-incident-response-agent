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
    execution_allowed,
    raw_would_allow_execution,
)
from incident_agent.contracts import (
    Alert,
    EvidenceRequest as ContractEvidenceRequest,
    IncidentState,
    Observations,
)
from incident_agent.evidence.models import EvidenceRequest, EvidenceType
from incident_agent.providers.prometheus import (
    BasePrometheusClient,
    FakePrometheusClient,
    PrometheusClient,
    PrometheusMetricsProvider,
    build_promql,
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
    assert "Memory increased from 300.0Mi to 900.0Mi" in result.summary


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
