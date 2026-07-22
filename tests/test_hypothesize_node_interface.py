from datetime import UTC, datetime

from incident_agent.contracts import Alert, Diagnosis, Evidence, IncidentState, Observations
from incident_agent.nodes.hypothesize import hypothesize


def _state(
    *,
    diagnosis: Diagnosis,
    logs: list[str] | None = None,
    events: list[str] | None = None,
    top_n: int = 3,
) -> IncidentState:
    state = IncidentState(
        incident_id="inc-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=logs or [], events=events or []),
        diagnosis=diagnosis,
    )
    state.observations.extra["top_n"] = top_n
    return state


def test_hypothesize_returns_top_n_and_probabilities() -> None:
    diagnosis = Diagnosis(
        summary="Missing Secret",
        category="Missing Secret",
        confidence=0.85,
        evidence=[Evidence(source="events", text="Missing secret / FailedMount secret detected")],
    )
    state = _state(
        diagnosis=diagnosis,
        events=[
            'Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume "secret": '
            'secret "db-credentials" not found'
        ],
        logs=["Back-off restarting failed container"],
    )
    hyps = hypothesize(state)["hypotheses"]
    assert isinstance(hyps, list)
    assert len(hyps) == 3
    assert all(0.0 <= h.likelihood <= 1.0 for h in hyps)
    assert abs(sum(h.likelihood for h in hyps) - 1.0) < 0.02
    assert hyps[0].description == "Secret not created"
    assert hyps[0].verification_checks
    assert hyps[0].remediation_key == "Missing Secret"
    assert hyps[0].evidence


def test_hypothesize_oom_produces_sre_ranked_causes() -> None:
    diagnosis = Diagnosis(
        summary="Resource Constraint (OOMKilled)",
        category="OOMKilled",
        confidence=0.9,
        evidence=[
            Evidence(source="events", text="OOMKilled event detected"),
            Evidence(source="other", text="Container terminated with exit code 137"),
        ],
    )
    state = _state(
        diagnosis=diagnosis,
        events=[
            "Warning  OOMKilled  kubelet  Container killed due to OOM",
            "Warning  BackOff  kubelet  Back-off restarting failed container",
        ],
        logs=["OOMKilled: Container was killed due to memory usage", "exit status 137"],
    )
    hyps = hypothesize(state)["hypotheses"]
    causes = [h.description for h in hyps]
    assert causes == ["Memory leak", "Memory limit too low", "Traffic spike"]
    assert hyps[0].likelihood > hyps[1].likelihood > hyps[2].likelihood
    assert "Check heap usage over time" in hyps[0].verification_checks
    assert all(h.remediation_key == "Resource Constraint (OOMKilled)" for h in hyps)


def test_hypothesize_never_returns_single_hypothesis() -> None:
    diagnosis = Diagnosis(summary="Unclear crash", category="Unknown", confidence=0.4)
    state = _state(diagnosis=diagnosis, logs=["something unrelated"], top_n=1)
    hyps = hypothesize(state)["hypotheses"]
    assert len(hyps) >= 2
    assert all(h.verification_checks for h in hyps)


def test_hypothesize_is_deterministic() -> None:
    diagnosis = Diagnosis(
        summary="Invalid Image Tag / Image Pull Error",
        category="Invalid Image",
        confidence=0.9,
        evidence=[Evidence(source="events", text="ErrImagePull event detected")],
    )
    state = _state(
        diagnosis=diagnosis,
        events=['Warning  Failed  kubelet  Error: ErrImagePull', "manifest unknown"],
    )
    a = hypothesize(state)["hypotheses"]
    b = hypothesize(state)["hypotheses"]
    assert [h.description for h in a] == [h.description for h in b]
    assert [h.likelihood for h in a] == [h.likelihood for h in b]
