"""Pydantic models for confidence calibration (safety gate inputs/outputs)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CalibrationModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PredictionOutcome(CalibrationModelBase):
    """One historical (predicted confidence → resolved?) pair for bin scaling."""

    predicted_confidence: float = Field(ge=0.0, le=1.0)
    resolved: bool


class CalibratedAssessment(CalibrationModelBase):
    """Safety-aware confidence after blending posterior, evidence, and memory."""

    raw_confidence: float = Field(ge=0.0, le=1.0)
    calibrated_confidence: float = Field(ge=0.0, le=1.0)
    execution_gated: bool
    risk_explanation: str
    verification_strength: float = Field(ge=0.0, le=1.0, default=0.0)
    historical_accuracy: float = Field(ge=0.0, le=1.0, default=0.0)
    bin_adjusted: bool = False
    safety_threshold: float = Field(ge=0.0, le=1.0, default=0.80)
