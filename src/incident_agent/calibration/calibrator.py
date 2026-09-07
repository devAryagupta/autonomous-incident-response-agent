"""Confidence calibration: bound overconfident posteriors before mutation.

Autonomous execution must depend on calibrated confidence, not raw Bayesian
posteriors. This module is pure (no I/O, no graph node side effects).
"""

from __future__ import annotations

from incident_agent.calibration.models import CalibratedAssessment, PredictionOutcome

# Blend weights: posterior + evidence quality dominate; memory is a prior check.
_WEIGHT_PREDICTED = 0.4
_WEIGHT_VERIFICATION = 0.4
_WEIGHT_HISTORICAL = 0.2

DEFAULT_SAFETY_THRESHOLD = 0.80
# Spec walkthrough / mutation gate used in safety tests.
MUTATION_SAFETY_THRESHOLD = 0.85

# Prediction bins for reliability correction (Platt-style binning).
# Bin is the bucket of confidence scores. past predictions are grouped by how much the confidence score they were confident in resolving the incident.
_BIN_EDGES: tuple[tuple[float, float], ...] = (
    (0.0, 0.5),
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.0),
)

# blend_confidence is a function that blends the predicted confidence, verification strength, and historical accuracy to produce a calibrated score.
def blend_confidence(
    predicted_confidence: float,
    verification_strength: float,
    historical_accuracy: float,
) -> float:
    """
    CalibratedScore = _WEIGHT_PREDICTED·Predicted + _WEIGHT_VERIFICATION·Verification + _WEIGHT_HISTORICAL·Historical.
    """
    score = (
        _clamp(predicted_confidence) * _WEIGHT_PREDICTED
        + _clamp(verification_strength) * _WEIGHT_VERIFICATION
        + _clamp(historical_accuracy) * _WEIGHT_HISTORICAL
    )
    return _clamp(score)


def _is_in_confidence_bin(
    confidence: float,
    *,
    lo: float,
    hi: float,
) -> bool:
    """True when confidence is in [lo, hi); the final bin also includes 1.0."""
    return lo <= confidence < hi or (hi == 1.0 and confidence == 1.0)

# empirical_bin_accuracy is a function that calculates the empirical success rate for predictions in a given confidence bin.by calculating the number of successful predictions in the bin divided by the total number of predictions in the bin.
def empirical_bin_accuracy(
    history: list[PredictionOutcome],
    *,
    lo: float,
    hi: float,
) -> float | None:
    """Empirical success rate for predictions in [lo, hi).

    The final bin includes confidence 1.0. Returns None when no predictions
    fall in the requested bin.
    """
    bin_outcomes = [
        outcome
        for outcome in history
        if _is_in_confidence_bin(outcome.predicted_confidence, lo=lo, hi=hi)
    ]
    if not bin_outcomes:
        return None
    successful = sum(outcome.resolved for outcome in bin_outcomes)
    return successful / len(bin_outcomes)

# bin scaling is the process of adjusting the confidence score of a prediction based on the empirical success rate of predictions in the same confidence bin.
# Example: predictions in [0.9, 1.0] historically succeed 85% of the time →
# raw 0.95 is pulled down toward 0.85 (conservative min of raw and A).
def apply_bin_scaling(
    confidence: float,
    history: list[PredictionOutcome],
) -> tuple[float, bool]:
    """Pull confidence down to this bin's empirical accuracy; never inflate it."""
    confidence = _clamp(confidence)

    if not history:
        return confidence, False

    for bin_low, bin_high in _BIN_EDGES:
        if not _is_in_confidence_bin(
            confidence,
            lo=bin_low,
            hi=bin_high,
        ):
            continue

        bin_accuracy = empirical_bin_accuracy(
            history,
            lo=bin_low,
            hi=bin_high,
        )
        if bin_accuracy is None:
            return confidence, False

        adjusted_confidence = min(confidence, bin_accuracy)
        was_scaled = adjusted_confidence != confidence
        return adjusted_confidence, was_scaled

    return confidence, False


def _clamp(value: float) -> float: # clamp is a function that clamps the value between 0 and 1.
    return min(max(float(value), 0.0), 1.0)


class ConfidenceCalibrator:
    """
    Bind predicted hypothesis confidence to verification quality and memory.

    Optional ``history`` enables bin reliability correction before the safety gate.
    """

    def __init__(
        self,
        *,
        safety_threshold: float = DEFAULT_SAFETY_THRESHOLD,
        history: list[PredictionOutcome] | None = None,
    ) -> None:
        self.safety_threshold = _clamp(safety_threshold)
        self._history = list(history or [])

    def calibrate(
        self,
        *,
        predicted_confidence: float,
        verification_strength: float,
        historical_accuracy: float,
    ) -> CalibratedAssessment:
        raw = _clamp(predicted_confidence)
        blended = blend_confidence(raw, verification_strength, historical_accuracy)
        scaled, bin_adjusted = apply_bin_scaling(blended, self._history)
        gated = scaled < self.safety_threshold
        explanation = _explain(
            raw=raw,
            blended=blended,
            calibrated=scaled,
            verification_strength=verification_strength,
            historical_accuracy=historical_accuracy,
            bin_adjusted=bin_adjusted,
            gated=gated,
            threshold=self.safety_threshold,
        )
        return CalibratedAssessment(
            raw_confidence=raw,
            calibrated_confidence=round(scaled, 6),
            execution_gated=gated,
            risk_explanation=explanation,
            verification_strength=_clamp(verification_strength),
            historical_accuracy=_clamp(historical_accuracy),
            bin_adjusted=bin_adjusted,
            safety_threshold=self.safety_threshold,
        )


def execution_allowed(assessment: CalibratedAssessment) -> bool:
    """Mutation gate: True only when calibrated confidence clears the safety threshold."""
    return not assessment.execution_gated


def raw_would_allow_execution(
    raw_confidence: float,
    *,
    safety_threshold: float,
) -> bool:
    """Counterfactual: would uncalibrated confidence have cleared the gate?"""
    return _clamp(raw_confidence) >= _clamp(safety_threshold)


def _explain(
    *,
    raw: float,
    blended: float,
    calibrated: float,
    verification_strength: float,
    historical_accuracy: float,
    bin_adjusted: bool,
    gated: bool,
    threshold: float,
) -> str:
    parts = [
        f"raw_posterior={raw:.3f}",
        f"blend={blended:.3f} (0.4·pred + 0.4·verify={verification_strength:.3f} "
        f"+ 0.2·history={historical_accuracy:.3f})",
        f"calibrated={calibrated:.3f}",
        f"threshold={threshold:.3f}",
    ]
    if bin_adjusted:
        parts.append("bin_reliability_scaled=true")
    if gated:
        parts.append("execution_gated=true (mutation blocked)")
    else:
        parts.append("execution_gated=false (mutation permitted)")
    return "; ".join(parts)
