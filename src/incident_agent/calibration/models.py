"""Pydantic models for confidence calibration (safety gate inputs/outputs)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field # pydantic is a library for data validation and settings management using Python type hints 


class CalibrationModelBase(BaseModel): # calibrationModelBase is a CHILD class of BaseModel.
    model_config = ConfigDict(extra="forbid", validate_assignment=True) # rules define how the model should behave when extra fields are provided or when an assignment is made to a field that is not defined in the model these are stored in the model_config dictionary . forbid means that extra fields will be rejected and validate_assignment means that assignments to fields that are not defined in the model will be rejected.


class PredictionOutcome(CalibrationModelBase): # predictionOutcome is a CHILD class of CalibrationModelBase.
    """One historical (predicted confidence → resolved?) pair for bin scaling."""

    predicted_confidence: float = Field(ge=0.0, le=1.0) # ge means greater than or equal to and le means less than or equal to and the field is a float between 0 and 1.
    resolved: bool # resolved is a boolean value that indicates whether the prediction was resolved or not.


class CalibratedAssessment(CalibrationModelBase): 
    """Safety-aware confidence after blending posterior, evidence, and memory."""

    raw_confidence: float = Field(ge=0.0, le=1.0) 
    # raw_confidence means the confidence of the prediction before it is calibrated.
    calibrated_confidence: float = Field(ge=0.0, le=1.0)
    # calibrated_confidence means the confidence of the prediction after it is calibrated.
    execution_gated: bool # execution_gated is a boolean value that indicates whether the prediction was executed or not.
    risk_explanation: str # risk_explanation is a string that explains the risk of the prediction.
    verification_strength: float = Field(ge=0.0, le=1.0, default=0.0) # verification_strength is a float between 0 and 1 that indicates the strength of the evidence for the incident
    historical_accuracy: float = Field(ge=0.0, le=1.0, default=0.0)# How often has this kind of diagnosis actually been right in the past
    bin_adjusted: bool = False # Did we lower the blended score because this confidence bucket has been overconfident historically
    safety_threshold: float = Field(ge=0.0, le=1.0, default=0.80)#The cutoff the calibrated score must meet before autonomous mutation is allowed. default is 0.80 
