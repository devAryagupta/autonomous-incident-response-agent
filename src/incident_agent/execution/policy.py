"""Execution policy: risk level × calibrated confidence → allow / human approval."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from incident_agent.calibration.models import CalibratedAssessment
from incident_agent.execution.actions.base import (
    AbstractActionHandler,
    ActionContext,
    RiskLevel,
)

# Minimum calibrated confidence required per risk level.
_CONFIDENCE_BY_RISK: dict[RiskLevel, float] = {
    RiskLevel.LOW: 0.75,
    RiskLevel.MEDIUM: 0.85,
    RiskLevel.HIGH: 0.95,
    RiskLevel.CRITICAL: 0.99,
}


class ExecutionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    allowed: bool
    reason: str
    requires_human_approval: bool
    required_confidence: float = 0.0
    calibrated_confidence: float = 0.0
    risk_level: int = 0


class ExecutionPolicy:
    """Gate mutations: no cluster touch without a passing policy decision."""

    def __init__(self, *, thresholds: dict[RiskLevel, float] | None = None) -> None:
        self._thresholds = dict(thresholds or _CONFIDENCE_BY_RISK)

    def required_confidence(self, risk_level: RiskLevel) -> float:
        return float(self._thresholds.get(risk_level, 0.99))

    def evaluate(
        self,
        *,
        calibrated_assessment: CalibratedAssessment,
        action_handler: AbstractActionHandler,
        action_context: ActionContext,
    ) -> ExecutionDecision:
        _ = action_context  # reserved for namespace / env policy extensions
        risk = action_handler.risk_level
        required = self.required_confidence(risk)
        score = float(calibrated_assessment.calibrated_confidence)

        if calibrated_assessment.execution_gated:
            return ExecutionDecision(
                allowed=False,
                reason=(
                    "execution_gated=true on CalibratedAssessment "
                    f"(calibrated={score:.3f}); route to human approval"
                ),
                requires_human_approval=True,
                required_confidence=required,
                calibrated_confidence=score,
                risk_level=int(risk),
            )

        if score < required:
            return ExecutionDecision(
                allowed=False,
                reason=(
                    f"calibrated_confidence={score:.3f} < required={required:.3f} "
                    f"for risk={risk.name}; route to human approval"
                ),
                requires_human_approval=True,
                required_confidence=required,
                calibrated_confidence=score,
                risk_level=int(risk),
            )

        return ExecutionDecision(
            allowed=True,
            reason=(
                f"policy allow: risk={risk.name} calibrated={score:.3f} "
                f">= required={required:.3f}"
            ),
            requires_human_approval=False,
            required_confidence=required,
            calibrated_confidence=score,
            risk_level=int(risk),
        )
