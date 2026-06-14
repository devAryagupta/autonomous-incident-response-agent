from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SchemaVersion = Literal["1"]


class ContractBase(BaseModel):
    """Base class for all contracts.

    - forbid extra fields so contracts don't silently drift
    - validate_assignment so state mutations are caught early
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_assignment=True)


class Alert(ContractBase):
    schema_version: SchemaVersion = "1"

    # identifiers
    alert_name: str
    severity: Literal["info", "warning", "critical"] = "warning"
    starts_at: datetime

    # common labels/metadata (source-agnostic)
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)

    # raw payload for traceability (optional, because we may start from synthetic fixtures)
    raw: dict[str, Any] | None = None


class Evidence(ContractBase):
    schema_version: SchemaVersion = "1"
    source: Literal["logs", "events", "describe", "metrics", "config", "other"] = "other"
    text: str


class Diagnosis(ContractBase):
    schema_version: SchemaVersion = "1"
    summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


class Hypothesis(ContractBase):
    schema_version: SchemaVersion = "1"
    hypothesis_id: str
    description: str
    likelihood: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)


FixActionKind = Literal[
    "restart_pod",
    "scale_deployment",
    "rollback",
    "patch_resource",
    "noop",
]


class FixAction(ContractBase):
    schema_version: SchemaVersion = "1"
    kind: FixActionKind
    target_ref: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str


class FixPlan(ContractBase):
    schema_version: SchemaVersion = "1"
    hypothesis_id: str | None = None
    actions: list[FixAction] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"
    notes: str | None = None


class ValidationResult(ContractBase):
    schema_version: SchemaVersion = "1"
    method: Literal["dry_run", "staging_replay", "simulation", "none"] = "none"
    success: bool
    diff: str | None = None
    notes: str | None = None


class ValidationVerdict(ContractBase):
    """Small interface for early-stage deterministic validation.

    This is intentionally minimal today. Later, we can attach:
    - dry-run outputs
    - staging replay results
    - simulation diffs
    without changing call sites that just need pass/fail + reason.
    """

    schema_version: SchemaVersion = "1"
    passed: bool
    reason: str


class ConfidenceScore(ContractBase):
    schema_version: SchemaVersion = "1"
    score: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(validation_alias="reason")


class Approval(ContractBase):
    schema_version: SchemaVersion = "1"
    approved: bool
    by: str | None = None
    comment: str | None = None
    at: datetime | None = None


class ExecutionResult(ContractBase):
    schema_version: SchemaVersion = "1"
    executed: bool
    success: bool
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IncidentState(ContractBase):
    """Top-level object the agent passes around (even before LangGraph exists)."""

    schema_version: SchemaVersion = "1"
    incident_id: str
    created_at: datetime

    # pipeline payloads
    alert: Alert
    diagnosis: Diagnosis | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    chosen_hypothesis_id: str | None = None
    fix_plan: FixPlan | None = None
    validation: list[ValidationResult] = Field(default_factory=list)
    confidence: ConfidenceScore | None = None
    approval: Approval | None = None
    execution: ExecutionResult | None = None

    # operational control
    replan_count: int = 0
    max_replans: int = 3

    # debug/trace
    log: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

