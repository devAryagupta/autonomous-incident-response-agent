"""Autonomous incident evaluation: multi-dimensional scorecards over decision traces."""

from incident_agent.eval.failures import (
    FAILURE_CATEGORIES,
    FailureCategory,
    classify_golden_failure,
    failure_histogram,
)
from incident_agent.eval.metrics import (
    aggregate_brier_score,
    brier_score,
    build_scorecard,
    causes_match,
    confidence_calibration_score,
    diagnosis_accuracy,
    evaluate_scenario,
    investigation_efficiency,
    normalize_cause,
    recovery_time_score,
    remediation_safety,
    score_trace,
    weighted_overall,
)
from incident_agent.eval.models import (
    DecisionTrace,
    EvaluationScorecard,
    EvidenceCall,
    MultiDimensionalScore,
    ScenarioResult,
)
from incident_agent.eval.trace import (
    decision_trace_from_state,
    plan_matches_resolution,
)

__all__ = [
    "DecisionTrace",
    "EvaluationScorecard",
    "EvidenceCall",
    "FAILURE_CATEGORIES",
    "FailureCategory",
    "MultiDimensionalScore",
    "ScenarioResult",
    "classify_golden_failure",
    "failure_histogram",
    "aggregate_brier_score",
    "brier_score",
    "build_scorecard",
    "causes_match",
    "confidence_calibration_score",
    "decision_trace_from_state",
    "diagnosis_accuracy",
    "evaluate_scenario",
    "investigation_efficiency",
    "normalize_cause",
    "plan_matches_resolution",
    "recovery_time_score",
    "remediation_safety",
    "score_trace",
    "weighted_overall",
]
