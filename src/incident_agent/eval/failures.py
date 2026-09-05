"""Classify a failed eval scenario by the first stage that went wrong.

This is not a new scorecard metric. It is a post-hoc label so we can say
"diagnosis was X%, remaining failures clustered at stage Y" instead of
tuning the agent against individual examples.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from incident_agent.contracts import IncidentState
from incident_agent.eval.metrics import normalize_cause
from incident_agent.routing import DIAGNOSIS_SCOPE_INVALID, INSUFFICIENT_CONFIDENCE, NOOP_DECISION

FailureCategory = Literal[
    "DATASET / EXPECTATION MISMATCH",
    "INSUFFICIENT EVIDENCE",
    "EXTRACTION ERROR",
    "DIAGNOSIS ERROR",
    "HYPOTHESIS ERROR",
    "VERIFICATION ERROR",
    "REMEDIATION ERROR",
    "EXECUTION ERROR",
    "OUTCOME VERIFICATION ERROR",
]

FAILURE_CATEGORIES: tuple[FailureCategory, ...] = (
    "DATASET / EXPECTATION MISMATCH",
    "INSUFFICIENT EVIDENCE",
    "EXTRACTION ERROR",
    "DIAGNOSIS ERROR",
    "HYPOTHESIS ERROR",
    "VERIFICATION ERROR",
    "REMEDIATION ERROR",
    "EXECUTION ERROR",
    "OUTCOME VERIFICATION ERROR",
)


def classify_golden_failure(
    *,
    diagnosis_correct: bool,
    fix_correct: bool,
    expected_hypothesis: str,
    expected_fix_kind: str | None,
    predicted_hypothesis: str | None,
    predicted_fix_kind: str | None,
    state: IncidentState,
) -> FailureCategory | None:
    """Return a stage label, or None when diagnosis and fix both match ground truth."""
    if diagnosis_correct and fix_correct:
        return _execution_or_outcome_failure(state)

    if state.diagnosis is None:
        if not state.observations.logs and not state.observations.events:
            return "EXTRACTION ERROR"
        return "DIAGNOSIS ERROR"

    if not diagnosis_correct:
        return "DIAGNOSIS ERROR"

    if _escalated_for_evidence(state):
        return "INSUFFICIENT EVIDENCE"

    hyp_error = _hypothesis_or_verification_error(
        state,
        expected_hypothesis=expected_hypothesis,
        predicted_hypothesis=predicted_hypothesis,
    )
    if hyp_error is not None:
        return hyp_error

    if not fix_correct:
        if predicted_fix_kind is None:
            return "INSUFFICIENT EVIDENCE"
        if _catalog_agrees_with_prediction(state, predicted_fix_kind, expected_fix_kind):
            return "DATASET / EXPECTATION MISMATCH"
        return "REMEDIATION ERROR"

    return _execution_or_outcome_failure(state) or "REMEDIATION ERROR"


def failure_histogram(categories: list[FailureCategory | None]) -> dict[str, int]:
    counted = Counter(cat for cat in categories if cat is not None)
    return {name: counted[name] for name in FAILURE_CATEGORIES if counted[name]}


def _escalated_for_evidence(state: IncidentState) -> bool:
    if state.decision == NOOP_DECISION and state.decision_reason == INSUFFICIENT_CONFIDENCE:
        return True
    if state.decision_reason == DIAGNOSIS_SCOPE_INVALID:
        return False
    return False


def _hypothesis_or_verification_error(
    state: IncidentState,
    *,
    expected_hypothesis: str,
    predicted_hypothesis: str | None,
) -> FailureCategory | None:
    if _same_label(predicted_hypothesis, expected_hypothesis):
        return None

    expected_in_list = any(
        _same_label(hyp.description, expected_hypothesis) for hyp in state.hypotheses
    )
    if not expected_in_list:
        return "HYPOTHESIS ERROR"

    for verification in state.hypothesis_verifications:
        if _same_label(verification.hypothesis, expected_hypothesis):
            if verification.result != "confirmed":
                return "VERIFICATION ERROR"
            if predicted_hypothesis and not _same_label(
                predicted_hypothesis, expected_hypothesis
            ):
                return "HYPOTHESIS ERROR"
    return "VERIFICATION ERROR"


def _same_label(left: str | None, right: str) -> bool:
    """Hypothesis labels are distinct even when they share a diagnosis alias bucket."""
    if not left:
        return False
    return normalize_cause(left) == normalize_cause(right)


def _catalog_agrees_with_prediction(
    state: IncidentState,
    predicted_fix_kind: str,
    expected_fix_kind: str | None,
) -> bool:
    """True when the agent picked its catalog winner, but GT expected a different fix."""
    if not expected_fix_kind:
        return False
    if predicted_fix_kind.strip().lower() == expected_fix_kind.strip().lower():
        return False
    chosen = state.chosen_remediation_id
    if chosen and state.remediation_options:
        top = state.remediation_options[0]
        return top.option_id == chosen and top.action == predicted_fix_kind
    return False


def _execution_or_outcome_failure(state: IncidentState) -> FailureCategory | None:
    result = state.execution
    if result is None:
        return None
    if result.status == "skipped" or not result.success:
        return "EXECUTION ERROR"
    if result.success and state.incident_resolved is False:
        return "OUTCOME VERIFICATION ERROR"
    if result.success and state.outcome_verification is not None:
        if not state.outcome_verification.resolved:
            return "OUTCOME VERIFICATION ERROR"
    return None
