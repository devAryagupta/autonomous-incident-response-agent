"""Confidence calibration: bound overconfidence before autonomous mutation."""

from incident_agent.calibration.calibrator import (
    DEFAULT_SAFETY_THRESHOLD,
    MUTATION_SAFETY_THRESHOLD,
    ConfidenceCalibrator,
    apply_bin_scaling,
    blend_confidence,
    empirical_bin_accuracy,
    execution_allowed,
    raw_would_allow_execution,
)
from incident_agent.calibration.models import CalibratedAssessment, PredictionOutcome

__all__ = [
    "DEFAULT_SAFETY_THRESHOLD",
    "MUTATION_SAFETY_THRESHOLD",
    "CalibratedAssessment",
    "ConfidenceCalibrator",
    "PredictionOutcome",
    "apply_bin_scaling",
    "blend_confidence",
    "empirical_bin_accuracy",
    "execution_allowed",
    "raw_would_allow_execution",
]
