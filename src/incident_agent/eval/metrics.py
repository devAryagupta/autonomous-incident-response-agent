"""Multi-dimensional evaluation engine for autonomous SRE agent runs.

Dimensions (higher is better unless noted):
1. Diagnosis Accuracy — confirmed hypothesis vs ground-truth cause
2. Investigation Efficiency — high-value evidence / total telemetry calls
3. Confidence Calibration — 1 − Brier(predicted, resolved)
4. Remediation Safety — parsimony vs lowest-risk adequate action
5. Recovery Time — normalized MTTR (steps + wall clock)

Pure functions only — no I/O, no graph mutations.
"""

from __future__ import annotations

from incident_agent.eval.models import (
    DecisionTrace,
    EvaluationScorecard,
    EvidenceCall,
    MultiDimensionalScore,
    ScenarioResult,
)

# Equal-weight overall scorecard (override via evaluate_scenario weights).
_DEFAULT_WEIGHTS: dict[str, float] = {
    "diagnosis_accuracy": 0.25,
    "investigation_efficiency": 0.15,
    "confidence_calibration": 0.20,
    "remediation_safety": 0.25,
    "recovery_time": 0.15,
}

# Normalization ceilings for MTTR scoring (beyond → score floors at 0).
_DEFAULT_MAX_STEPS = 20
_DEFAULT_MAX_SECONDS = 600.0

# Cause aliases so diagnosis labels and hypothesis descriptions can match.
_CAUSE_ALIASES: dict[str, frozenset[str]] = {
    "oomkilled": frozenset(
        {
            "oomkilled",
            "oom",
            "memory leak",
            "memory limit too low",
            "resource constraint (oomkilled)",
            "resource constraint",
        }
    ),
    "application failure": frozenset(
        {
            "application failure",
            "application crash",
            "application bug / unhandled exception",
            "unhandled exception",
            "startup exception",
            "app bug",
        }
    ),
    "invalid configuration": frozenset(
        {
            "invalid configuration",
            "secret not created",
            "missing secret",
            "secret not found",
            "missing environment variable",
            "bad configuration",
            "config parse error",
        }
    ),
}


def normalize_cause(label: str) -> str:
    """Lowercase, collapse whitespace for exact / alias matching."""
    return " ".join(label.strip().lower().split())


def causes_match(predicted: str | None, ground_truth: str) -> bool:
    """Exact normalized match, shared alias bucket, or label inside narrative GT."""
    if predicted is None or not predicted.strip():
        return False
    left = normalize_cause(predicted)
    right = normalize_cause(ground_truth)
    if left == right:
        return True
    # HF-style narratives: predicted short label appears inside root_cause prose.
    if len(left) >= 3 and (left in right or right in left):
        return True
    left_bucket = _alias_bucket(left)
    right_bucket = _alias_bucket(right)
    if left_bucket is not None and left_bucket == right_bucket:
        return True
    if left_bucket is not None:
        if left_bucket in right:
            return True
        for alias in _CAUSE_ALIASES[left_bucket]:
            if len(alias) >= 3 and alias in right:
                return True
    return False


def _alias_bucket(normalized: str) -> str | None:
    for bucket, aliases in _CAUSE_ALIASES.items():
        if normalized == bucket or normalized in aliases:
            return bucket
    return None


def diagnosis_accuracy(
    confirmed_hypothesis: str | None,
    ground_truth_cause: str,
    *,
    predicted_diagnosis_category: str | None = None,
) -> float:
    """
    1.0 if confirmed hypothesis (or diagnosis category) matches ground truth.

    Prefers confirmed hypothesis; falls back to diagnosis category.
    """
    if causes_match(confirmed_hypothesis, ground_truth_cause):
        return 1.0
    if causes_match(predicted_diagnosis_category, ground_truth_cause):
        return 1.0
    return 0.0


def investigation_efficiency(
    evidence_calls: list[EvidenceCall],
    *,
    high_value_queries: list[str] | None = None,
) -> float:
    """
    Ratio of high-value evidence requests to total telemetry calls.

    Redundant calls never count as high-value. Empty trace → 0.0.
    """
    if not evidence_calls:
        return 0.0

    hv_names = {normalize_cause(q) for q in (high_value_queries or [])}
    high_value = 0
    for call in evidence_calls:
        if call.redundant:
            continue
        if call.high_value or normalize_cause(call.query) in hv_names:
            high_value += 1

    return high_value / len(evidence_calls)


def brier_score(predicted_confidence: float, resolved: bool) -> float:
    """
    Single-outcome Brier score: (p − o)² where o ∈ {0, 1}.

    Lower is better. Perfect calibration on a correct resolve at p=1 → 0.
    """
    outcome = 1.0 if resolved else 0.0
    p = min(max(float(predicted_confidence), 0.0), 1.0)
    return (p - outcome) ** 2


def confidence_calibration_score(predicted_confidence: float, resolved: bool) -> float:
    """Higher-is-better calibration: 1 − Brier."""
    return 1.0 - brier_score(predicted_confidence, resolved)


def aggregate_brier_score(
    pairs: list[tuple[float, bool]],
) -> float:
    """Mean Brier score across runs (lower is better)."""
    if not pairs:
        return 0.0
    return sum(brier_score(p, o) for p, o in pairs) / len(pairs)


def remediation_safety(
    selected_action_risk: int,
    min_adequate_action_risk: int,
    *,
    resolved: bool,
) -> float:
    """
    Parsimony score: reward the lowest-risk adequate action.

    - Unresolved → 0.0
    - Selected risk == min adequate → 1.0
    - Selected risk lower than min adequate (under-powered) → 0.0
    - Selected risk higher than needed → decays toward 0 as risk gap grows
    """
    if not resolved:
        return 0.0

    selected = int(selected_action_risk)
    minimum = int(min_adequate_action_risk)
    if selected < minimum:
        return 0.0
    if selected == minimum:
        return 1.0

    # Gap of 1 risk level → 0.67, gap of 2 → 0.33, gap of 3 → 0.0
    max_gap = 3
    gap = min(selected - minimum, max_gap)
    return max(0.0, 1.0 - (gap / max_gap))


def recovery_time_score(
    steps: int,
    elapsed_seconds: float,
    *,
    max_steps: int = _DEFAULT_MAX_STEPS,
    max_seconds: float = _DEFAULT_MAX_SECONDS,
) -> float:
    """
    Normalized MTTR score in [0, 1] (higher = faster recovery).

    Blends step efficiency (60%) and wall-clock (40%).
    """
    step_component = 1.0 - min(max(steps, 0), max_steps) / max_steps
    time_component = 1.0 - min(max(elapsed_seconds, 0.0), max_seconds) / max_seconds
    return 0.6 * step_component + 0.4 * time_component


def weighted_overall(
    scores: dict[str, float],
    weights: dict[str, float] | None = None,
) -> float:
    """Weighted mean of dimension scores (weights renormalized)."""
    w = dict(weights or _DEFAULT_WEIGHTS)
    total_w = sum(w.get(k, 0.0) for k in scores)
    if total_w <= 0:
        return 0.0
    return sum(scores[k] * w.get(k, 0.0) for k in scores) / total_w


def score_trace(
    trace: DecisionTrace,
    *,
    max_steps: int = _DEFAULT_MAX_STEPS,
    max_seconds: float = _DEFAULT_MAX_SECONDS,
    weights: dict[str, float] | None = None,
) -> MultiDimensionalScore:
    """Compute all five dimensions for one decision trace."""
    diag = diagnosis_accuracy(
        trace.confirmed_hypothesis,
        trace.ground_truth_cause,
        predicted_diagnosis_category=trace.predicted_diagnosis_category,
    )
    invest = investigation_efficiency(
        trace.evidence_calls,
        high_value_queries=trace.high_value_queries,
    )
    brier = brier_score(trace.predicted_confidence, trace.resolved)
    calib = 1.0 - brier
    safety = remediation_safety(
        trace.selected_action_risk,
        trace.min_adequate_action_risk,
        resolved=trace.resolved,
    )
    recovery = recovery_time_score(
        trace.steps,
        trace.elapsed_seconds,
        max_steps=max_steps,
        max_seconds=max_seconds,
    )
    dims = {
        "diagnosis_accuracy": diag,
        "investigation_efficiency": invest,
        "confidence_calibration": calib,
        "remediation_safety": safety,
        "recovery_time": recovery,
    }
    overall = weighted_overall(dims, weights)
    return MultiDimensionalScore(
        diagnosis_accuracy=round(diag, 6),
        investigation_efficiency=round(invest, 6),
        confidence_calibration=round(calib, 6),
        remediation_safety=round(safety, 6),
        recovery_time=round(recovery, 6),
        brier_score=round(brier, 6),
        mttr_steps=trace.steps,
        mttr_seconds=trace.elapsed_seconds,
        overall=round(overall, 6),
        details={
            "selected_action": trace.selected_action,
            "ground_truth_cause": trace.ground_truth_cause,
            "confirmed_hypothesis": trace.confirmed_hypothesis,
            "telemetry_calls": len(trace.evidence_calls),
        },
    )


def evaluate_scenario(
    trace: DecisionTrace,
    *,
    max_steps: int = _DEFAULT_MAX_STEPS,
    max_seconds: float = _DEFAULT_MAX_SECONDS,
    weights: dict[str, float] | None = None,
) -> ScenarioResult:
    """Trace → ScenarioResult (scores + identity)."""
    scores = score_trace(
        trace,
        max_steps=max_steps,
        max_seconds=max_seconds,
        weights=weights,
    )
    return ScenarioResult(
        scenario_id=trace.scenario_id,
        ground_truth_cause=trace.ground_truth_cause,
        resolved=trace.resolved,
        scores=scores,
        trace=trace,
    )


def build_scorecard(results: list[ScenarioResult]) -> EvaluationScorecard:
    """Aggregate ScenarioResults into a multi-run EvaluationScorecard."""
    if not results:
        return EvaluationScorecard(
            scenario_count=0,
            mean_diagnosis_accuracy=0.0,
            mean_investigation_efficiency=0.0,
            mean_confidence_calibration=0.0,
            mean_remediation_safety=0.0,
            mean_recovery_time=0.0,
            mean_overall=0.0,
            aggregate_brier_score=0.0,
            mean_mttr_seconds=0.0,
            results=[],
        )

    n = len(results)
    pairs = [
        (r.trace.predicted_confidence, r.resolved) for r in results
    ]
    return EvaluationScorecard(
        scenario_count=n,
        mean_diagnosis_accuracy=round(
            sum(r.scores.diagnosis_accuracy for r in results) / n, 6
        ),
        mean_investigation_efficiency=round(
            sum(r.scores.investigation_efficiency for r in results) / n, 6
        ),
        mean_confidence_calibration=round(
            sum(r.scores.confidence_calibration for r in results) / n, 6
        ),
        mean_remediation_safety=round(
            sum(r.scores.remediation_safety for r in results) / n, 6
        ),
        mean_recovery_time=round(
            sum(r.scores.recovery_time for r in results) / n, 6
        ),
        mean_overall=round(sum(r.scores.overall for r in results) / n, 6),
        aggregate_brier_score=round(aggregate_brier_score(pairs), 6),
        mean_mttr_seconds=round(
            sum(r.scores.mttr_seconds for r in results) / n, 6
        ),
        results=list(results),
    )
