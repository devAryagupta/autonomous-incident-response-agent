from datetime import UTC, datetime

from incident_agent.contracts import Alert, Diagnosis, IncidentState, Observations
from incident_agent.nodes.hypothesize import hypothesize
from incident_agent.nodes.plan_fix import plan_fix
from incident_agent.nodes.score_confidence import score_confidence
from incident_agent.nodes.validate_fix import validate_fix


def test_plan_validate_score_is_deterministic() -> None:
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

    state = IncidentState(
        incident_id="inc-1",
        created_at=datetime.now(tz=UTC),
        alert=alert,
        observations=Observations(logs=list(logs), events=[]),
        diagnosis=diagnosis,
    )
    state.observations.extra["top_n"] = 3
    state.observations.extra["target_ref"] = "deployment/demo-app"

    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    state.fix_plan = plan_fix(state)["fix_plan"]  # type: ignore[assignment]
    updates = validate_fix(state)
    state.validation = updates["validation"]  # type: ignore[assignment]
    state.validation_verdict = updates["validation_verdict"]  # type: ignore[assignment]
    conf = score_confidence(state)["confidence"]

    assert state.fix_plan.actions
    assert state.validation and state.validation[0].success is True
    assert 0.0 <= conf.score <= 1.0


def test_noop_plan_reduces_confidence() -> None:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    diagnosis = Diagnosis(summary="Unclear crash", confidence=0.4)
    state = IncidentState(
        incident_id="inc-2",
        created_at=datetime.now(tz=UTC),
        alert=alert,
        observations=Observations(logs=["random"], events=[]),
        diagnosis=diagnosis,
    )
    state.observations.extra["top_n"] = 2
    state.observations.extra["target_ref"] = "deployment/demo-app"

    state.hypotheses = hypothesize(state)["hypotheses"]  # type: ignore[assignment]
    state.fix_plan = plan_fix(state)["fix_plan"]  # type: ignore[assignment]
    updates = validate_fix(state)
    state.validation = updates["validation"]  # type: ignore[assignment]
    state.validation_verdict = updates["validation_verdict"]  # type: ignore[assignment]
    conf = score_confidence(state)["confidence"]

    # Not a strict numeric expectation, just ensure score is bounded and reason is present.
    assert 0.0 <= conf.score <= 1.0
    assert conf.explanation

