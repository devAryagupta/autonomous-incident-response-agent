"""Bayesian verification loop + Prometheus metrics evidence."""

from __future__ import annotations

import pytest

from incident_agent.evidence.models import EvidenceRequest, EvidenceType, TelemetryResult
from incident_agent.hypothesis.models import HypothesisState, HypothesisStatus
from incident_agent.providers.prometheus import (
    PrometheusMetricsProvider,
    StaticPrometheusClient,
)
from incident_agent.verification.bayesian import (
    CONFIRM_THRESHOLD,
    EVIDENCE_CONTINUOUS_MEMORY_GROWTH,
    LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH_SPEC,
    PARTIAL_THRESHOLD,
    bayesian_update,
    status_from_posterior,
    update_for_continuous_memory_growth,
)
from incident_agent.verification.evaluator import (
    detect_monotonically_increasing,
    detect_step_increase_or_spike,
)
from incident_agent.verification.loop import (
    initial_oom_hypotheses,
    plan_oom_metric_evidence,
    run_bayesian_verification_loop,
)


def test_detect_monotonically_increasing_memory_series() -> None:
    assert detect_monotonically_increasing([300.0, 450.0, 700.0, 900.0]) is True
    assert detect_monotonically_increasing([900.0, 700.0, 450.0, 300.0]) is False
    assert detect_monotonically_increasing([100.0]) is False


def test_detect_step_increase_or_spike() -> None:
    assert detect_step_increase_or_spike([50.0, 55.0, 5000.0, 4800.0]) is True
    assert detect_step_increase_or_spike([40.0, 45.0, 50.0, 55.0]) is False


def test_bayesian_posterior_exact_math_spec_likelihoods() -> None:
    """Priors [0.40, 0.40, 0.20] × spec likelihoods [0.95, 0.40, 0.05]."""
    priors = [0.40, 0.40, 0.20]
    likelihoods = [0.95, 0.40, 0.05]
    unnormalized = [p * lik for p, lik in zip(priors, likelihoods, strict=True)]
    total = sum(unnormalized)
    expected = [u / total for u in unnormalized]

    assert total == pytest.approx(0.55)
    assert expected[0] == pytest.approx(0.38 / 0.55)
    assert expected[1] == pytest.approx(0.16 / 0.55)
    assert expected[2] == pytest.approx(0.01 / 0.55)

    hyps = initial_oom_hypotheses()
    updated = bayesian_update(
        hyps,
        likelihoods=LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH_SPEC,
        evidence_label=EVIDENCE_CONTINUOUS_MEMORY_GROWTH,
    )
    by_id = {h.id: h for h in updated}

    assert by_id["H1"].posterior_probability == pytest.approx(expected[0], rel=1e-5)
    assert by_id["H2"].posterior_probability == pytest.approx(expected[1], rel=1e-5)
    assert by_id["H3"].posterior_probability == pytest.approx(expected[2], rel=1e-5)
    assert by_id["H1"].status == HypothesisStatus.PARTIAL  # ≈0.691 < 0.70
    assert by_id["H2"].status == HypothesisStatus.PARTIAL
    assert by_id["H3"].status == HypothesisStatus.REJECTED


def test_status_thresholds() -> None:
    assert CONFIRM_THRESHOLD == 0.70
    assert PARTIAL_THRESHOLD == 0.20
    assert status_from_posterior(0.70) == HypothesisStatus.CONFIRMED
    assert status_from_posterior(0.69) == HypothesisStatus.PARTIAL
    assert status_from_posterior(0.19) == HypothesisStatus.REJECTED


def test_end_to_end_prometheus_bayesian_loop() -> None:
    """
    Mock Prometheus memory series → evaluator → Bayesian update.

    Walkthrough outcome: H1 CONFIRMED, H2 PARTIAL, H3 REJECTED.
    """
    target = "deployment/payment-service"
    requests = plan_oom_metric_evidence(target, time_range="30m")
    assert requests[0].type is EvidenceType.METRIC
    assert requests[0].query == "container_memory_usage_bytes"
    assert requests[0].time_range == "30m"

    client = StaticPrometheusClient(
        {
            "container_memory_usage_bytes": [300.0, 450.0, 700.0, 900.0],
            "http_requests_per_second": [40.0, 45.0, 50.0, 55.0],
        }
    )
    provider = PrometheusMetricsProvider(client)
    telemetry = [provider.query_telemetry(req) for req in requests]

    assert telemetry[0].datapoints == [300.0, 450.0, 700.0, 900.0]
    assert detect_monotonically_increasing(telemetry[0].datapoints) is True

    result = run_bayesian_verification_loop(
        hypotheses=initial_oom_hypotheses(),
        telemetry=telemetry,
    )
    assert result.memory_continuously_increasing is True
    assert result.traffic_spike_detected is False

    by_id = {h.id: h for h in result.hypotheses}
    assert by_id["H1"].posterior_probability == pytest.approx(0.72, abs=0.02)
    assert by_id["H2"].posterior_probability == pytest.approx(0.26, abs=0.02)
    assert by_id["H3"].posterior_probability == pytest.approx(0.02, abs=0.01)
    assert by_id["H1"].status == HypothesisStatus.CONFIRMED
    assert by_id["H2"].status == HypothesisStatus.PARTIAL
    assert by_id["H3"].status == HypothesisStatus.REJECTED
    assert EVIDENCE_CONTINUOUS_MEMORY_GROWTH in by_id["H1"].supporting_evidence


def test_default_update_matches_walkthrough_posteriors() -> None:
    updated = update_for_continuous_memory_growth(initial_oom_hypotheses())
    by_id = {h.id: h for h in updated}
    assert by_id["H1"].status == HypothesisStatus.CONFIRMED
    assert by_id["H2"].status == HypothesisStatus.PARTIAL
    assert by_id["H3"].status == HypothesisStatus.REJECTED


def test_prometheus_provider_fulfills_contract_evidence_request() -> None:
    from datetime import UTC, datetime

    from incident_agent.contracts import Alert
    from incident_agent.contracts import EvidenceRequest as ContractEvidenceRequest
    from incident_agent.contracts import IncidentState, Observations

    client = StaticPrometheusClient(
        {"container_memory_usage_bytes": [300.0, 450.0, 700.0, 900.0]}
    )
    provider = PrometheusMetricsProvider(client)
    state = IncidentState(
        incident_id="inc-prom-1",
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
            request_id="er-mem-1",
            type="metric",
            query="container_memory_usage_bytes",
            target="deployment/payment-service",
        ),
        state=state,
    )
    assert result.success
    assert result.data["series"] == [300.0, 450.0, 700.0, 900.0]
    assert "Memory increased from 300Mi to 900Mi" in result.summary


def test_telemetry_and_hypothesis_schemas() -> None:
    req = EvidenceRequest(
        type=EvidenceType.METRIC,
        query="container_memory_usage_bytes",
        target="deployment/payment-service",
        time_range="30m",
    )
    tele = TelemetryResult(request=req, datapoints=[300.0, 450.0])
    assert tele.request.type is EvidenceType.METRIC

    hyp = HypothesisState(
        id="H1",
        description="Memory leak",
        prior_probability=0.4,
        posterior_probability=0.4,
        status=HypothesisStatus.UNVERIFIED,
    )
    assert hyp.status is HypothesisStatus.UNVERIFIED
