"""Failure-stage labels for golden eval — not new scorecard metrics."""

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    ExecutionResult,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    Observations,
    OutcomeVerification,
    RemediationOption,
    RiskLevel,
)
from incident_agent.eval.failures import classify_golden_failure, failure_histogram
from incident_agent.routing import INSUFFICIENT_CONFIDENCE, NOOP_DECISION


def _state(**overrides: object) -> IncidentState:
    stamp = datetime.now(tz=UTC)
    base = dict(
        incident_id="inc-eval-fail",
        created_at=stamp,
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=stamp),
        observations=Observations(logs=["OOMKilled"], events=["Back-off"]),
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9),
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                description="Memory limit too low",
                likelihood=0.8,
            )
        ],
        chosen_hypothesis_id="h1",
    )
    base.update(overrides)
    return IncidentState.model_validate(base)


def test_no_label_when_diagnosis_and_fix_match() -> None:
    assert (
        classify_golden_failure(
            diagnosis_correct=True,
            fix_correct=True,
            expected_hypothesis="Memory limit too low",
            expected_fix_kind="increase_memory_limit",
            predicted_hypothesis="Memory limit too low",
            predicted_fix_kind="increase_memory_limit",
            state=_state(),
        )
        is None
    )


def test_diagnosis_error_is_first_stage() -> None:
    label = classify_golden_failure(
        diagnosis_correct=False,
        fix_correct=False,
        expected_hypothesis="Memory limit too low",
        expected_fix_kind="increase_memory_limit",
        predicted_hypothesis="Unhandled exception in application",
        predicted_fix_kind="rollback_deployment",
        state=_state(),
    )
    assert label == "DIAGNOSIS ERROR"


def test_insufficient_evidence_when_gate_escalates() -> None:
    label = classify_golden_failure(
        diagnosis_correct=True,
        fix_correct=False,
        expected_hypothesis="Missing environment variable",
        expected_fix_kind="patch_env_var",
        predicted_hypothesis="Missing environment variable",
        predicted_fix_kind=None,
        state=_state(decision=NOOP_DECISION, decision_reason=INSUFFICIENT_CONFIDENCE),
    )
    assert label == "INSUFFICIENT EVIDENCE"


def test_verification_error_when_expected_hyp_not_confirmed() -> None:
    state = _state(
        hypothesis_verifications=[
            HypothesisVerification(
                hypothesis_id="h1",
                hypothesis="Memory limit too low",
                result="inconclusive",
                expected_evidence=["OOMKilled"],
                observed_evidence=[],
            )
        ]
    )
    label = classify_golden_failure(
        diagnosis_correct=True,
        fix_correct=False,
        expected_hypothesis="Memory limit too low",
        expected_fix_kind="increase_memory_limit",
        predicted_hypothesis="Memory leak",
        predicted_fix_kind="increase_memory_limit",
        state=state,
    )
    assert label == "VERIFICATION ERROR"


def test_dataset_mismatch_when_catalog_winner_differs_from_expected_fix() -> None:
    state = _state(
        chosen_remediation_id="r1-restart_pod",
        remediation_options=[
            RemediationOption(
                option_id="r1-restart_pod",
                action="restart_pod",
                expected_effect="temp",
                risk=RiskLevel.LOW,
            )
        ],
    )
    label = classify_golden_failure(
        diagnosis_correct=True,
        fix_correct=False,
        expected_hypothesis="Memory limit too low",
        expected_fix_kind="increase_memory_limit",
        predicted_hypothesis="Memory limit too low",
        predicted_fix_kind="restart_pod",
        state=state,
    )
    assert label == "DATASET / EXPECTATION MISMATCH"


def test_outcome_verification_error_after_successful_execute() -> None:
    stamp = datetime.now(tz=UTC)
    state = _state(
        execution=ExecutionResult(
            executed=True,
            success=True,
            status="success",
            action="increase_memory_limit",
            summary="ok",
            started_at=stamp,
            finished_at=stamp,
        ),
        incident_resolved=False,
        outcome_verification=OutcomeVerification(
            resolved=False,
            expected_outcome=["pod becomes healthy"],
            unmet_expectations=["pod becomes healthy"],
        ),
    )
    label = classify_golden_failure(
        diagnosis_correct=True,
        fix_correct=True,
        expected_hypothesis="Memory limit too low",
        expected_fix_kind="increase_memory_limit",
        predicted_hypothesis="Memory limit too low",
        predicted_fix_kind="increase_memory_limit",
        state=state,
    )
    assert label == "OUTCOME VERIFICATION ERROR"


def test_histogram_skips_passes() -> None:
    counts = failure_histogram(["DIAGNOSIS ERROR", None, "DIAGNOSIS ERROR", "REMEDIATION ERROR"])
    assert counts == {"DIAGNOSIS ERROR": 2, "REMEDIATION ERROR": 1}
