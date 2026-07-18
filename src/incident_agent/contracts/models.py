from __future__ import annotations

from datetime import datetime
from enum import StrEnum
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


class FixActionType(StrEnum):
    RESTART_POD = "restart_pod"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    SCALE_DEPLOYMENT = "scale_deployment"
    PATCH_CONFIG = "patch_config"
    PATCH_RESOURCE = "patch_resource"
    NOOP = "noop"


class FixAction(ContractBase):
    schema_version: SchemaVersion = "1"
    action_type: FixActionType = Field(validation_alias="kind")
    target: str = Field(validation_alias="target_ref")
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FixPlan(ContractBase):
    schema_version: SchemaVersion = "1"
    hypothesis_id: str | None = None
    actions: list[FixAction] = Field(default_factory=list)
    risk: RiskLevel = Field(default=RiskLevel.LOW)
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


class ResourceRef(ContractBase):
    """Source-agnostic pointer to the impacted resource.

    Keep this generic initially (no Kubernetes client types).
    """

    schema_version: SchemaVersion = "1"
    system: str = "kubernetes"
    namespace: str | None = None
    kind: str | None = None
    name: str | None = None
    uid: str | None = None
    cluster: str | None = None


class Observations(ContractBase):
    """Raw signals the agent reasons over.

    Deterministic baseline: only logs/events strings.
    Later: add structured metrics/describe outputs without changing call sites.
    """

    schema_version: SchemaVersion = "1"
    logs: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


IncidentPhase = Literal[
    "ingest",
    "diagnose",
    "hypothesize",
    "plan_fix",
    "validate_fix",
    "score_confidence",
    "replan",
    "approve",
    "execute",
    "done",
]


class IncidentState(ContractBase):
    """Top-level state passed between nodes (even before LangGraph exists).

    Design goals:
    - One contract for the whole pipeline (alert → diagnose → hypothesize → plan → validate → score)
    - Deterministic-first: nodes can run without any external systems
    - Backward-compatible: new fields are optional or have safe defaults
    """

    schema_version: SchemaVersion = "1"
    incident_id: str
    created_at: datetime

    # identity / routing
    thread_id: str | None = None
    phase: IncidentPhase = "ingest"
    route: str | None = None

    # impacted resource (optional in deterministic baselines)
    resource: ResourceRef | None = None

    # pipeline payloads
    alert: Alert
    observations: Observations = Field(default_factory=Observations)
    diagnosis: Diagnosis | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    chosen_hypothesis_id: str | None = None
    fix_plan: FixPlan | None = None
    validation: list[ValidationResult] = Field(default_factory=list)
    validation_verdict: ValidationVerdict | None = None
    confidence: ConfidenceScore | None = None
    confidence_score: float | None = None
    # MemoryProvider output (empty under NoMemory)
    similar_incidents: list[dict[str, Any]] = Field(default_factory=list)
    approval: Approval | None = None
    execution: ExecutionResult | None = None

    # operational control (confidence replan loop)
    replan_count: int = 0
    max_replans: int = 2

    # debug/trace
    log: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

