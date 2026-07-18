from datetime import UTC, datetime

from incident_agent.contracts import Alert, Diagnosis, IncidentState, Observations
from incident_agent.nodes.hypothesize import hypothesize


def test_hypothesize_returns_top_n_and_probabilities() -> None:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    diagnosis = Diagnosis(summary="Container exits immediately", confidence=0.6)
    logs_and_events = [
        (
            "Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume \"secret\": "
            "secret \"db-credentials\" not found"
        ),
        "Back-off restarting failed container",
    ]

    state = IncidentState(
        incident_id="inc-1",
        created_at=datetime.now(tz=UTC),
        alert=alert,
        observations=Observations(logs=list(logs_and_events), events=[]),
        diagnosis=diagnosis,
    )
    state.observations.extra["top_n"] = 3
    hyps = hypothesize(state)["hypotheses"]
    assert isinstance(hyps, list)
    assert len(hyps) == 3

    # Always multiple hypotheses
    assert len(hyps) >= 2

    # Likelihood is a probability
    assert all(0.0 <= h.likelihood <= 1.0 for h in hyps)  # type: ignore[attr-defined]

    # Should rank Missing Secret highly due to the log line.
    assert hyps[0].description in {"Missing Secret", "Misconfigured Volume Mount / Missing Path"}  # type: ignore[attr-defined]


def test_hypothesize_never_returns_single_hypothesis() -> None:
    alert = Alert(alert_name="CrashLoopBackOff", severity="warning", starts_at=datetime.now(tz=UTC))
    diagnosis = Diagnosis(summary="Container exits immediately", confidence=0.6)
    state = IncidentState(
        incident_id="inc-2",
        created_at=datetime.now(tz=UTC),
        alert=alert,
        observations=Observations(logs=["something unrelated"], events=[]),
        diagnosis=diagnosis,
    )
    state.observations.extra["top_n"] = 1
    hyps = hypothesize(state)["hypotheses"]
    assert len(hyps) >= 2

