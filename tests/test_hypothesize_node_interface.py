from datetime import UTC, datetime

from incident_agent.contracts import Alert, Diagnosis
from incident_agent.nodes.hypothesize import hypothesize


def test_hypothesize_returns_top_n_and_probabilities() -> None:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    diagnosis = Diagnosis(summary="Container exits immediately", confidence=0.6)
    logs = [
        (
            "Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume \"secret\": "
            "secret \"db-credentials\" not found"
        ),
        "Back-off restarting failed container",
    ]

    hyps = hypothesize(alert=alert, logs=logs, diagnosis=diagnosis, top_n=3)
    assert isinstance(hyps, list)
    assert len(hyps) == 3

    # Always multiple hypotheses
    assert len(hyps) >= 2

    # Likelihood is a probability
    assert all(0.0 <= h.likelihood <= 1.0 for h in hyps)

    # Should rank Missing Secret highly due to the log line.
    assert hyps[0].description in {"Missing Secret", "Misconfigured Volume Mount / Missing Path"}


def test_hypothesize_never_returns_single_hypothesis() -> None:
    alert = Alert(alert_name="CrashLoopBackOff", severity="warning", starts_at=datetime.now(tz=UTC))
    diagnosis = Diagnosis(summary="Container exits immediately", confidence=0.6)
    hyps = hypothesize(alert=alert, logs=["something unrelated"], diagnosis=diagnosis, top_n=1)
    assert len(hyps) >= 2

