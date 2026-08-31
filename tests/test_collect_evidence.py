from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    Evidence,
    EvidenceResult,
    EvidenceRequest,
    Hypothesis,
    IncidentState,
    Observations,
)
from incident_agent.evidence import plan_evidence_requests
from incident_agent.nodes.collect_evidence import collect_evidence
from incident_agent.nodes.hypothesize import hypothesize
from incident_agent.nodes.verify_hypotheses import verify_hypotheses
from incident_agent.providers import (
    DryRunExecutionProvider,
    NoMemoryProvider,
    ProviderBundle,
    SyntheticMetricsProvider,
    SyntheticObservationProvider,
    default_providers,
)


def _oom_state() -> IncidentState:
    state = IncidentState(
        incident_id="inc-evidence-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
            labels={"service": "payment-service"},
        ),
        observations=Observations(
            logs=["OOMKilled: Container was killed due to memory usage", "exit status 137"],
            events=[
                "Warning  OOMKilled  kubelet  Container killed due to OOM",
                "Warning  BackOff  kubelet  Back-off restarting failed container",
            ],
            extra={"top_n": 3, "target_ref": "deployment/payment-service"},
        ),
        diagnosis=Diagnosis(
            summary="Resource Constraint (OOMKilled)",
            category="OOMKilled",
            confidence=0.9,
            evidence=[
                Evidence(source="events", text="OOMKilled event detected"),
                Evidence(source="other", text="Container terminated with exit code 137"),
            ],
        ),
    )
    return state


def test_planner_emits_metric_request_for_memory_leak() -> None:
    state = _oom_state()
    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    requests = plan_evidence_requests(state)
    assert requests
    assert any(
        r.type == "metric"
        and r.query == "container_memory_usage_bytes"
        and r.target == "deployment/payment-service"
        for r in requests
    )
    assert any(r.query == "restart_history" for r in requests)


def test_collect_evidence_fulfills_requests_and_enriches_observations() -> None:
    state = _oom_state()
    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    updates = collect_evidence(state, providers=default_providers())
    requests = updates["evidence_requests"]
    results = updates["evidence_results"]
    obs = updates["observations"]

    assert len(results) == len(requests)
    assert all(r.success for r in results)
    assert any("Memory increased from" in (r.summary or "") for r in results)
    assert obs.extra["metrics"]["memory_mi"]["end"] == 900
    assert any("Memory increased from" in line for line in obs.logs)


def test_synthetic_metrics_provider_executes_evidence_request() -> None:
    state = _oom_state()
    req = EvidenceRequest(
        request_id="er-1",
        type="metric",
        query="container_memory_usage_bytes",
        target="payment-service",
    )
    result = SyntheticMetricsProvider().execute_evidence_request(req, state=state)
    assert result.success
    assert "900Mi" in result.summary
    assert result.data["memory_mi"]["start"] == 200


def test_synthetic_observation_provider_executes_restart_history() -> None:
    state = _oom_state()
    req = EvidenceRequest(
        request_id="er-2",
        type="event",
        query="restart_history",
        target="payment-service",
    )
    result = SyntheticObservationProvider().execute_evidence_request(req, state=state)
    assert result.success
    assert "restart_count" in result.data
    assert "Restart history" in result.summary


def test_collected_evidence_feeds_verification() -> None:
    state = _oom_state()
    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    collected = collect_evidence(state, providers=default_providers())
    state.evidence_requests = collected["evidence_requests"]  # type: ignore[assignment]
    state.evidence_results = collected["evidence_results"]  # type: ignore[assignment]
    state.observations = collected["observations"]  # type: ignore[assignment]

    verified = verify_hypotheses(state)
    hyps = verified["hypotheses"]
    verifications = {v.hypothesis: v for v in verified["hypothesis_verifications"]}
    assert hyps[0].description == "Memory leak"
    assert verifications["Memory leak"].result == "confirmed"
    assert any("900Mi" in e for e in verifications["Memory leak"].observed_evidence)


def test_provider_evidence_can_confirm_hypothesis_without_hypothesis_injection() -> None:
    class _InjectedTracebackProvider(SyntheticObservationProvider):
        def execute_evidence_request(
            self,
            request: EvidenceRequest,
            *,
            state: IncidentState,
        ) -> EvidenceResult:
            if request.query == "previous_container_logs":
                return EvidenceResult(
                    request_id=request.request_id,
                    type=request.type,
                    query=request.query,
                    target=request.target,
                    success=True,
                    summary=(
                        "Traceback (most recent call last):; "
                        "Unhandled exception in bootstrap()"
                    ),
                    data={
                        "provider": "synthetic-test",
                        "query": request.query,
                        "lines": [
                            "Traceback (most recent call last):",
                            "Unhandled exception in bootstrap()",
                        ],
                    },
                )
            return super().execute_evidence_request(request, state=state)

    state = IncidentState(
        incident_id="inc-evidence-provider-confirm",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
            labels={"service": "api-gateway"},
        ),
        observations=Observations(
            logs=[
                "process exited unexpectedly",
                "Exit Code: 1",
            ],
            events=["Warning BackOff pod/api-gateway Back-off restarting failed container"],
            extra={"top_n": 2, "target_ref": "deployment/api-gateway"},
        ),
        diagnosis=Diagnosis(
            summary="Application Failure",
            category="Application Failure",
            confidence=0.7,
            evidence=[Evidence(source="other", text="generic crash")],
        ),
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1-unhandled_exception",
                description="Unhandled exception in application",
                likelihood=1.0,
                remediation_key="Application Bug / Unhandled Exception",
            )
        ],
    )

    before = verify_hypotheses(state)
    before_v = before["hypothesis_verifications"][0]
    assert before_v.result == "inconclusive"
    assert not before_v.observed_required_evidence

    providers = ProviderBundle(
        observations=_InjectedTracebackProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=NoMemoryProvider(),
    )
    collected = collect_evidence(state, providers=providers)
    state.evidence_requests = collected["evidence_requests"]  # type: ignore[assignment]
    state.evidence_results = collected["evidence_results"]  # type: ignore[assignment]
    state.observations = collected["observations"]  # type: ignore[assignment]

    after = verify_hypotheses(state)
    after_v = after["hypothesis_verifications"][0]
    assert after_v.result == "confirmed"
    assert any("traceback" in item.lower() for item in after_v.observed_required_evidence)
