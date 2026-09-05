"""Standalone OOM metric walkthrough — not a LangGraph node.

plan metrics → evaluate series → optional Continuous Memory Growth update.

Uses ``HypothesisState`` and ``evidence.models.TelemetryResult``.
The live graph never calls this. Traffic-spike detection is recorded only;
it does not update posteriors. See docs/VERIFICATION_STACKS.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.evidence.models import EvidenceRequest, EvidenceType, TelemetryResult
from incident_agent.hypothesis.models import HypothesisState, HypothesisStatus
from incident_agent.verification.bayesian import (
    EVIDENCE_CONTINUOUS_MEMORY_GROWTH,
    update_for_continuous_memory_growth,
)
from incident_agent.verification.evaluator import (
    detect_monotonically_increasing,
    detect_step_increase_or_spike,
)


@dataclass(frozen=True, slots=True)
class BayesianVerificationResult:
    """Outcome of one Bayesian verification pass over hypotheses."""

    hypotheses: tuple[HypothesisState, ...]
    evidence_requests: tuple[EvidenceRequest, ...]
    telemetry: tuple[TelemetryResult, ...]
    memory_continuously_increasing: bool
    traffic_spike_detected: bool


def plan_oom_metric_evidence(target: str, *, time_range: str = "30m") -> list[EvidenceRequest]:
    """Evidence plan for OOM / CrashLoop discrimination via metrics."""
    return [
        EvidenceRequest(
            type=EvidenceType.METRIC,
            query="container_memory_usage_bytes",
            target=target,
            time_range=time_range,
        ),
        EvidenceRequest(
            type=EvidenceType.METRIC,
            query="http_requests_per_second",
            target=target,
            time_range=time_range,
        ),
    ]


def initial_oom_hypotheses() -> list[HypothesisState]:
    """Priors for the OOMKilled discrimination scenario."""
    return [
        HypothesisState(
            id="H1",
            description="Memory leak",
            prior_probability=0.40,
            posterior_probability=0.40,
            status=HypothesisStatus.UNVERIFIED,
        ),
        HypothesisState(
            id="H2",
            description="Memory limit too low",
            prior_probability=0.40,
            posterior_probability=0.40,
            status=HypothesisStatus.UNVERIFIED,
        ),
        HypothesisState(
            id="H3",
            description="Traffic spike",
            prior_probability=0.20,
            posterior_probability=0.20,
            status=HypothesisStatus.UNVERIFIED,
        ),
    ]


def run_bayesian_verification_loop(
    *,
    hypotheses: list[HypothesisState],
    telemetry: list[TelemetryResult],
) -> BayesianVerificationResult:
    """
    Evaluate telemetry and Bayesian-update hypotheses.

    When memory series is monotonically increasing, applies the Continuous
    Memory Growth likelihood matrix. Traffic-spike detection is recorded for
    callers; a dedicated likelihood update can be composed later.
    """
    memory_increasing = False
    traffic_spike = False

    for result in telemetry:
        query = result.request.query
        if query == "container_memory_usage_bytes":
            memory_increasing = detect_monotonically_increasing(result.datapoints)
        elif query == "http_requests_per_second":
            traffic_spike = detect_step_increase_or_spike(result.datapoints)

    updated = list(hypotheses)
    if memory_increasing:
        updated = update_for_continuous_memory_growth(updated)
        # Tag supporting evidence already applied inside bayesian_update.
        _ = EVIDENCE_CONTINUOUS_MEMORY_GROWTH

    requests = tuple(r.request for r in telemetry)
    return BayesianVerificationResult(
        hypotheses=tuple(updated),
        evidence_requests=requests,
        telemetry=tuple(telemetry),
        memory_continuously_increasing=memory_increasing,
        traffic_spike_detected=traffic_spike,
    )
