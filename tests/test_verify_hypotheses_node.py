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
