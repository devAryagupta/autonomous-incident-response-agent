"""Schemas for multi-dimensional autonomous SRE evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvalModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceCall(EvalModelBase):
    """One telemetry / evidence fetch recorded in a decision trace."""

    query: str
    type: str = "metric"  # metric | log | event | describe
    high_value: bool = False
    redundant: bool = False


class DecisionTrace(EvalModelBase):
    """
    Captured agent run for evaluation (Synthetic Incident → Agent → Trace).

    Ground truth is scenario-authored; agent fields are filled from the run.
    """

    scenario_id: str
    ground_truth_cause: str
    confirmed_hypothesis: str | None = None
    predicted_diagnosis_category: str | None = None

    evidence_calls: list[EvidenceCall] = Field(default_factory=list)
    # Explicit high-value query names when calls lack high_value flags.
    high_value_queries: list[str] = Field(default_factory=list)

    predicted_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    resolved: bool = False

    selected_action: str | None = None
    selected_action_risk: int = Field(ge=1, le=4, default=2)
    # Lowest risk among actions that would have resolved this scenario.
    min_adequate_action_risk: int = Field(ge=1, le=4, default=1)

    steps: int = Field(ge=0, default=0)
    elapsed_seconds: float = Field(ge=0.0, default=0.0)
    started_at: datetime | None = None
    resolved_at: datetime | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)


class MultiDimensionalScore(EvalModelBase):
    """Per-scenario scores in [0, 1] (higher is better) plus raw diagnostics."""

    diagnosis_accuracy: float = Field(ge=0.0, le=1.0)
    investigation_efficiency: float = Field(ge=0.0, le=1.0)
    confidence_calibration: float = Field(ge=0.0, le=1.0)
    remediation_safety: float = Field(ge=0.0, le=1.0)
    recovery_time: float = Field(ge=0.0, le=1.0)

    # Raw / lower-is-better companions for calibration & MTTR.
    brier_score: float = Field(ge=0.0, le=1.0, default=0.0)
    mttr_steps: int = Field(ge=0, default=0)
    mttr_seconds: float = Field(ge=0.0, default=0.0)

    overall: float = Field(ge=0.0, le=1.0, default=0.0)
    details: dict[str, Any] = Field(default_factory=dict)


class ScenarioResult(EvalModelBase):
    """One simulated incident evaluation: trace + multi-dimensional scores."""

    scenario_id: str
    ground_truth_cause: str
    resolved: bool
    scores: MultiDimensionalScore
    trace: DecisionTrace


class EvaluationScorecard(EvalModelBase):
    """Aggregate scorecard across many scenario runs."""

    scenario_count: int = Field(ge=0)
    mean_diagnosis_accuracy: float = Field(ge=0.0, le=1.0)
    mean_investigation_efficiency: float = Field(ge=0.0, le=1.0)
    mean_confidence_calibration: float = Field(ge=0.0, le=1.0)
    mean_remediation_safety: float = Field(ge=0.0, le=1.0)
    mean_recovery_time: float = Field(ge=0.0, le=1.0)
    mean_overall: float = Field(ge=0.0, le=1.0)
    aggregate_brier_score: float = Field(ge=0.0, le=1.0)
    mean_mttr_seconds: float = Field(ge=0.0, default=0.0)
    results: list[ScenarioResult] = Field(default_factory=list)
