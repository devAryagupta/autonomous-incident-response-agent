from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    Evidence,
    Hypothesis,
    IncidentState,
    Observations,
)
from incident_agent.nodes.hypothesize import hypothesize
from incident_agent.nodes.verify_hypotheses import verify_hypotheses


def _base_state(*, logs: list[str], events: list[str], diagnosis: Diagnosis) -> IncidentState:
    state = IncidentState(
        incident_id="inc-verify-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=logs, events=events),
        diagnosis=diagnosis,
    )
    state.observations.extra["top_n"] = 3
    return state


def test_verify_confirms_secret_and_updates_posterior() -> None:
    diagnosis = Diagnosis(
        summary="Invalid Configuration",
        category="Invalid Configuration",
        confidence=0.85,
        evidence=[Evidence(source="events", text="Secret missing")],
    )
    state = _base_state(
        diagnosis=diagnosis,
        events=[
            'Warning  FailedMount kubelet  MountVolume.SetUp failed for volume "secret": '
            'secret "db-credentials" not found'
        ],
        logs=["Back-off restarting failed container"],
    )
    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    prior_top = state.hypotheses[0].likelihood

    updates = verify_hypotheses(state)
    verifications = updates["hypothesis_verifications"]
    hyps = updates["hypotheses"]

    assert updates["chosen_hypothesis_id"] == hyps[0].hypothesis_id
    top_v = next(v for v in verifications if v.hypothesis_id == hyps[0].hypothesis_id)
    assert top_v.hypothesis == "Secret not created"
    assert top_v.result == "confirmed"
    assert top_v.supporting_evidence
    assert top_v.required_evidence
    assert top_v.expected_evidence
    assert top_v.observed_evidence
    assert top_v.confidence_delta > 0
    assert hyps[0].likelihood >= prior_top
    assert abs(sum(h.likelihood for h in hyps) - 1.0) < 0.02


def test_verify_memory_growth_confirms_leak_and_downranks_traffic() -> None:
    diagnosis = Diagnosis(
        summary="Resource Constraint (OOMKilled)",
        category="OOMKilled",
        confidence=0.9,
        evidence=[
            Evidence(source="events", text="OOMKilled event detected"),
            Evidence(source="other", text="Container terminated with exit code 137"),
        ],
    )
    state = _base_state(
        diagnosis=diagnosis,
        events=[
            "Warning  OOMKilled  kubelet  Container killed due to OOM",
            "Warning  BackOff  kubelet  Back-off restarting failed container",
        ],
        logs=[
            "OOMKilled: Container was killed due to memory usage",
            "memory increased from 200Mi to 900Mi",
            "exit status 137",
        ],
    )
    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    updates = verify_hypotheses(state)
    hyps = updates["hypotheses"]
    verifications = {v.hypothesis: v for v in updates["hypothesis_verifications"]}

    assert hyps[0].description == "Memory leak"
    assert verifications["Memory leak"].result == "confirmed"
    assert verifications["Memory leak"].required_evidence
    assert any("900Mi" in e for e in verifications["Memory leak"].observed_evidence)
    assert verifications["Traffic spike"].result == "inconclusive"
    assert hyps[0].likelihood > hyps[-1].likelihood


def test_verify_contradiction_reduces_likelihood() -> None:
    state = _base_state(
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.8),
        events=["Warning  OOMKilled  kubelet  Container killed due to OOM"],
        logs=["traffic spike rps=5000", "exit status 137"],
    )
    # Force a weak traffic-only prior ordering to exercise contradiction/support.
    state.hypotheses = [
        Hypothesis(
            hypothesis_id="h1-memory_limit_too_low",
            description="Memory limit too low",
            likelihood=0.60,
            verification_checks=["Check configured memory requests/limits"],
            remediation_key="Resource Constraint (OOMKilled)",
        ),
        Hypothesis(
            hypothesis_id="h2-traffic_spike",
            description="Traffic spike",
            likelihood=0.30,
            verification_checks=["Check request rate metrics around the OOM window"],
            remediation_key="Resource Constraint (OOMKilled)",
        ),
        Hypothesis(
            hypothesis_id="h3-memory_leak",
            description="Memory leak",
            likelihood=0.10,
            verification_checks=["Check heap usage over time"],
            remediation_key="Resource Constraint (OOMKilled)",
        ),
    ]
    updates = verify_hypotheses(state)
    verifications = {v.hypothesis: v for v in updates["hypothesis_verifications"]}
    assert verifications["Traffic spike"].result == "confirmed"
    assert verifications["Memory limit too low"].result in {"contradicted", "inconclusive"}
    # Traffic should rise after confirmation of load signals.
    ranked = [h.description for h in updates["hypotheses"]]
    assert ranked[0] == "Traffic spike"


def test_verify_missing_env_beats_secret_when_secret_signals_absent() -> None:
    diagnosis = Diagnosis(
        summary="Invalid Configuration",
        category="Invalid Configuration",
        confidence=0.9,
        evidence=[
            Evidence(source="logs", text="KeyError: 'DATABASE_URL'"),
            Evidence(source="logs", text="missing environment variable DATABASE_URL"),
        ],
    )
    state = _base_state(
        diagnosis=diagnosis,
        events=[
            "Normal Started pod/email-worker Started container email-worker",
            "Warning BackOff pod/email-worker Back-off restarting failed container",
        ],
        logs=[
            "Traceback (most recent call last):",
            "KeyError: 'DATABASE_URL'",
            "missing environment variable DATABASE_URL",
            "No FailedMount secret events for Deployment/email-worker",
            "Secret existence check inconclusive for Deployment/email-worker",
        ],
    )
    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    updates = verify_hypotheses(state)

    ranked = updates["hypotheses"]
    verifications = {v.hypothesis: v for v in updates["hypothesis_verifications"]}
    assert ranked[0].description == "Missing environment variable"
    assert verifications["Missing environment variable"].result == "confirmed"
    assert verifications["Missing environment variable"].observed_required_evidence
    assert verifications["Secret not created"].result == "contradicted"
    assert verifications["Secret not created"].observed_contradicting_evidence


def test_hypothesis_injected_evidence_cannot_self_confirm() -> None:
    state = _base_state(
        diagnosis=Diagnosis(
            summary="Application Failure",
            category="Application Failure",
            confidence=0.88,
            evidence=[
                Evidence(source="logs", text="panic"),
                Evidence(source="logs", text="fatal error"),
            ],
        ),
        events=["Warning BackOff pod/api-gateway Back-off restarting failed container"],
        logs=[
            "panic: startup initialization failed in bootstrap()",
            "fatal error: could not initialize router state",
            "Exit Code: 1",
        ],
    )
    state.hypotheses = [
        Hypothesis(
            hypothesis_id="h1-unhandled_exception",
            description="Unhandled exception in application",
            likelihood=0.60,
            evidence=[Evidence(source="other", text="Application raised an unhandled exception")],
            remediation_key="Application Bug / Unhandled Exception",
        ),
        Hypothesis(
            hypothesis_id="h2-fatal_runtime_error",
            description="Fatal runtime error",
            likelihood=0.30,
            evidence=[Evidence(source="other", text="Fatal runtime signal observed in application logs")],
            remediation_key="Application Bug / Unhandled Exception",
        ),
        Hypothesis(
            hypothesis_id="h3-startup_regression",
            description="Startup regression after deploy",
            likelihood=0.10,
            evidence=[
                Evidence(
                    source="other",
                    text="Crash loop started after application startup path changed",
                )
            ],
            remediation_key="Application Bug / Unhandled Exception",
        ),
    ]

    updates = verify_hypotheses(state)
    verifications = {v.hypothesis: v for v in updates["hypothesis_verifications"]}

    unhandled = verifications["Unhandled exception in application"]
    assert unhandled.result == "inconclusive"
    assert "Unhandled exception or traceback appears in logs" in unhandled.expected_evidence
    assert not unhandled.observed_required_evidence
    assert not unhandled.observed_supporting_evidence
    assert all(
        "application raised an unhandled exception" not in item.lower()
        for item in unhandled.observed_evidence
    )

    assert verifications["Fatal runtime error"].result == "confirmed"


def test_startup_regression_requires_real_rollout_signal() -> None:
    state = _base_state(
        diagnosis=Diagnosis(
            summary="Application Failure",
            category="Application Failure",
            confidence=0.75,
            evidence=[Evidence(source="other", text="generic crash signal")],
        ),
        events=["Warning BackOff pod/api-gateway Back-off restarting failed container"],
        logs=["startup path failed", "Exit Code: 1"],
    )
    state.hypotheses = [
        Hypothesis(
            hypothesis_id="h1-startup_regression",
            description="Startup regression after deploy",
            likelihood=1.0,
            evidence=[
                Evidence(
                    source="other",
                    text="Crash loop started after application startup path changed",
                )
            ],
            remediation_key="Application Bug / Unhandled Exception",
        )
    ]

    updates = verify_hypotheses(state)
    verification = updates["hypothesis_verifications"][0]

    assert verification.hypothesis == "Startup regression after deploy"
    assert verification.result == "inconclusive"
    assert "Deploy/revision change signal is present" in verification.expected_evidence
    assert not verification.observed_required_evidence
