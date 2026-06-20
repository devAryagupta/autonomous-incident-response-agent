from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.datasets.crashloopbackoff.schema import (
    Alert,
    CrashLoopBackOffIncident,
    ExpectedFix,
    K8sRef,
)
from incident_agent.datasets.eval import CrashLoopPrediction, HypothesisPrediction, evaluate_crashloop


def _incident(
    *,
    incident_id: str,
    category: str,
    root_cause: str,
    expected_fix_kind: str,
) -> CrashLoopBackOffIncident:
    now = datetime.now(tz=UTC)
    return CrashLoopBackOffIncident(
        incident_id=incident_id,
        created_at=now,
        target=K8sRef(namespace="default", kind="Deployment", name="demo"),
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=now),
        logs=["log"],
        events=["event"],
        category=category,  # type: ignore[arg-type]
        root_cause=root_cause,
        expected_fix=ExpectedFix(summary="fix", kind=expected_fix_kind, kubectl_hint=None),
        distractors=[],
    )


def test_expanded_benchmark_metrics() -> None:
    incidents = [
        _incident(
            incident_id="i1",
            category="missing_secret",
            root_cause="Secret db-credentials missing or wrong namespace",
            expected_fix_kind="create_secret",
        ),
        _incident(
            incident_id="i2",
            category="missing_env_var",
            root_cause="Missing required environment variable DB_HOST",
            expected_fix_kind="patch_deployment_env",
        ),
    ]

    predictions = [
        CrashLoopPrediction(
            incident_id="i1",
            predicted_diagnosis=incidents[0].root_cause,
            predicted_fix_kind="create_secret",
            predicted_validation_passed=True,
            predicted_confidence_score=0.8,
            predicted_hypotheses=[HypothesisPrediction(cause="Missing Secret", likelihood=0.8)],
        ),
        CrashLoopPrediction(
            incident_id="i2",
            predicted_diagnosis="wrong diagnosis",
            predicted_fix_kind="create_secret",  # wrong
            predicted_validation_passed=False,
            predicted_confidence_score=0.2,
            predicted_hypotheses=[HypothesisPrediction(cause="Missing Environment Variable")],
        ),
    ]

    report = evaluate_crashloop(incidents, predictions)

    assert report.counts.total == 2
    assert report.counts.predictions_found == 2

    assert report.diagnosis_accuracy == 0.5
    assert report.hypothesis_top1_accuracy == 1.0
    assert report.hypothesis_top3_accuracy == 1.0
    assert report.fix_kind_accuracy == 0.5
    assert report.validation_pass_rate == 0.5

    assert report.average_confidence == 0.5
    # With pairs: (0.8, True), (0.2, False) and 10 bins:
    # ECE = 0.5*|1.0-0.8| + 0.5*|0.0-0.2| = 0.2
    assert report.confidence_calibration_ece is not None
    assert abs(report.confidence_calibration_ece - 0.2) < 1e-9

