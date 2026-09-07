"""LLM boundary contracts.

The LLM can suggest additional reasoning options, but it must never control
routing, approval, or execution decisions directly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from incident_agent.contracts.models import ContractBase, EvidenceRequestType, SchemaVersion

SuggestionStage = Literal["hypothesize", "collect_evidence", "plan_fix", "verify_outcome"]
SuggestionKind = Literal["hypothesis", "evidence", "remediation", "analysis"]
SuggestionScalar = str | int | float | bool

BLOCKED_CONTROL_KEYS = frozenset(
    {
        "approval",
        "approved",
        "confidence_threshold",
        "decision",
        "decision_reason",
        "execute",
        "execution",
        "execution_plan",
        "fix_plan",
        "max_replans",
        "phase",
        "replan_count",
        "route",
    }
)


class LLMSuggestionRequest(ContractBase):
    """Context passed to an LLM suggestion provider.

    The request intentionally contains reasoning context only, not mutable
    control-plane fields.
    """

    schema_version: SchemaVersion = "1"
    incident_id: str
    stage: SuggestionStage
    diagnosis_category: str | None = None
    diagnosis_summary: str | None = None
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    target_ref: str | None = None
    max_suggestions: int = Field(ge=1, le=10, default=3)


class LLMSuggestion(ContractBase):
    """One model-produced suggestion for a specific reasoning stage."""

    schema_version: SchemaVersion = "1"
    suggestion_id: str
    stage: SuggestionStage
    kind: SuggestionKind
    summary: str
    rationale: str = ""
    hypothesis_id: str | None = None
    target_ref: str | None = None
    metadata: dict[str, SuggestionScalar] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _metadata_must_not_control_pipeline(
        cls,
        value: dict[str, SuggestionScalar],
    ) -> dict[str, SuggestionScalar]:
        blocked = sorted(
            key for key in value if key.strip().lower().replace("-", "_") in BLOCKED_CONTROL_KEYS
        )
        if blocked:
            raise ValueError(
                "LLM metadata cannot set control fields. Blocked keys: "
                + ", ".join(blocked)
            )
        return value


class LLMSuggestionResponse(ContractBase):
    """Provider output. Suggestions are advisory only."""

    schema_version: SchemaVersion = "1"
    provider: str
    model: str
    stage: SuggestionStage
    suggestions: list[LLMSuggestion] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0, default=0)
    prompt_tokens: int | None = Field(ge=0, default=None)
    completion_tokens: int | None = Field(ge=0, default=None)

    @model_validator(mode="after")
    def _suggestions_must_match_stage(self) -> LLMSuggestionResponse:
        bad = [item.suggestion_id for item in self.suggestions if item.stage != self.stage]
        if bad:
            raise ValueError(
                "All suggestions must match response stage. Bad suggestion_ids: "
                + ", ".join(bad)
            )
        return self


class AdvisoryHypothesisCandidate(ContractBase):
    """Accepted suggestion for deterministic hypothesis evaluation."""

    schema_version: SchemaVersion = "1"
    suggestion_id: str
    description: str
    rationale: str = ""
    metadata: dict[str, SuggestionScalar] = Field(default_factory=dict)


class AdvisoryEvidenceRequestCandidate(ContractBase):
    """Accepted suggestion for deterministic evidence collection planning."""

    schema_version: SchemaVersion = "1"
    suggestion_id: str
    type: EvidenceRequestType
    query: str
    target: str
    rationale: str = ""
    hypothesis_id: str | None = None
    metadata: dict[str, SuggestionScalar] = Field(default_factory=dict)


class SuggestionRejection(ContractBase):
    """Reason an LLM suggestion was rejected by ingestion."""

    schema_version: SchemaVersion = "1"
    suggestion_id: str | None = None
    reason: str
    detail: str


class SuggestionIngestionResult(ContractBase):
    """Deterministic gate result for one stage response."""

    schema_version: SchemaVersion = "1"
    incident_id: str
    stage: SuggestionStage
    provider: str
    model: str
    accepted_hypotheses: list[AdvisoryHypothesisCandidate] = Field(default_factory=list)
    accepted_evidence_requests: list[AdvisoryEvidenceRequestCandidate] = Field(
        default_factory=list
    )
    rejected: list[SuggestionRejection] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    accepted_count: int = Field(ge=0, default=0)
    rejected_count: int = Field(ge=0, default=0)
