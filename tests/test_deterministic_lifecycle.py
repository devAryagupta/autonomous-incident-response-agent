from datetime import UTC, datetime

from incident_agent.contracts import Alert
from incident_agent.pipeline import run_deterministic_lifecycle


def test_complete_deterministic_lifecycle_runs() -> None:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    state = run_deterministic_lifecycle(
        incident_id="inc-1",
        alert=alert,
        logs=[
            (
                "Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume \"secret\": "
                "secret \"db-credentials\" not found"
            ),
            "Back-off restarting failed container",
        ],
        top_n=3,
        target_ref="deployment/demo-app",
        # Stage-0 lifecycle smoke test stays on the single-pass path.
        confidence_threshold=0.0,
    )

    assert state.diagnosis is not None
    assert len(state.hypotheses) >= 2
    assert state.fix_plan is not None and state.fix_plan.actions
    assert state.validation and state.validation[0].success is True
    assert state.confidence is not None
    assert 0.0 <= state.confidence.score <= 1.0

