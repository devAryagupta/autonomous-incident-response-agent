"""Hypothesis state for the Bayesian verification loop.

Separate from `contracts.Hypothesis` (Stage-0 graph payload). Convert at the
boundary when wiring into IncidentState.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class HypothesisModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class HypothesisStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    CONFIRMED = "CONFIRMED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class HypothesisState(HypothesisModelBase):
    """Prior → posterior belief for one root-cause hypothesis."""

    id: str
    description: str
    prior_probability: float = Field(ge=0.0, le=1.0)
    posterior_probability: float = Field(ge=0.0, le=1.0)
    status: HypothesisStatus = HypothesisStatus.UNVERIFIED
    supporting_evidence: list[str] = Field(default_factory=list)
