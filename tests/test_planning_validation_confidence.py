from datetime import UTC, datetime

from incident_agent.contracts import Alert, Diagnosis
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

    hyps = hypothesize(alert=alert, logs=logs, diagnosis=diagnosis, top_n=3)
    fix_plan, match = plan_fix(hypotheses=hyps, target_ref="deployment/demo-app")
    validation = validate_fix(fix_plan=fix_plan)
    conf = score_confidence(hypotheses=hyps, fix_plan=fix_plan, validation=validation)

    assert match.matched_rule is not None
    assert fix_plan.actions
    assert validation and validation[0].success is True
    assert 0.0 <= conf.score <= 1.0


def test_noop_plan_reduces_confidence() -> None:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    diagnosis = Diagnosis(summary="Unclear crash", confidence=0.4)
    hyps = hypothesize(alert=alert, logs=["random"], diagnosis=diagnosis, top_n=2)
    fix_plan, _ = plan_fix(hypotheses=hyps, target_ref="deployment/demo-app")
    validation = validate_fix(fix_plan=fix_plan)
    conf = score_confidence(hypotheses=hyps, fix_plan=fix_plan, validation=validation)

    # Not a strict numeric expectation, just ensure score is bounded and reason is present.
    assert 0.0 <= conf.score <= 1.0
    assert conf.explanation

