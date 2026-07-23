"""Confidence calibration: bound overconfident posteriors before mutation.

Autonomous execution must depend on calibrated confidence, not raw Bayesian
posteriors. This module is pure (no I/O, no graph node side effects).
"""

from __future__ import annotations

from incident_agent.calibration.models import CalibratedAssessment, PredictionOutcome

# Blend weights: posterior + evidence quality dominate; memory is a prior check.
_W_PREDICTED = 0.4
_W_VERIFICATION = 0.4
_W_HISTORICAL = 0.2

DEFAULT_SAFETY_THRESHOLD = 0.80
# Spec walkthrough / mutation gate used in safety tests.
MUTATION_SAFETY_THRESHOLD = 0.85

# Prediction bins for reliability correction (Platt-style binning).
_BIN_EDGES: tuple[tuple[float, float], ...] = (
    (0.0, 0.5),
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.0),
)


def blend_confidence(
    predicted_confidence: float,
    verification_strength: float,
    historical_accuracy: float,
) -> float:
    """
    CalibratedScore = 0.4·Predicted + 0.4·Verification + 0.2·Historical.
    """
    score = (
        _clamp(predicted_confidence) * _W_PREDICTED
        + _clamp(verification_strength) * _W_VERIFICATION
        + _clamp(historical_accuracy) * _W_HISTORICAL
    )
    return _clamp(score)


def empirical_bin_accuracy(
    history: list[PredictionOutcome],
    *,
    lo: float,
    hi: float,
) -> float | None:
    """Empirical success rate for predictions in ``[lo, hi)`` (hi inclusive at 1.0)."""
    in_bin = [
        row
        for row in history
        if (lo <= row.predicted_confidence < hi)
        or (hi >= 1.0 and row.predicted_confidence == 1.0)
    ]
    if not in_bin:
        return None
    return sum(1 for row in in_bin if row.resolved) / len(in_bin)


def apply_bin_scaling(
    confidence: float,
    history: list[PredictionOutcome],
) -> tuple[float, bool]:
    """
    If the bin containing ``confidence`` has empirical accuracy A, scale toward A.

    Example: predictions in [0.9, 1.0] historically succeed 85% of the time →
    raw 0.95 is pulled down toward 0.85 (conservative min of raw and A).
    """
    if not history:
        return _clamp(confidence), False

    for lo, hi in _BIN_EDGES:
        in_range = (lo <= confidence < hi) or (hi >= 1.0 and confidence >= lo)
        if not in_range:
            continue
        accuracy = empirical_bin_accuracy(history, lo=lo, hi=hi)
        if accuracy is None:
            return _clamp(confidence), False
        # Never inflate confidence above the blended score; only bound overconfidence.
        adjusted = min(_clamp(confidence), float(accuracy))
        return adjusted, adjusted != _clamp(confidence)
    return _clamp(confidence), False


def _clamp(value: float) -> float:
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
