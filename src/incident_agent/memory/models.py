"""Incident memory domain entities (persistence-agnostic)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from incident_agent.contracts import ResolutionAssessment


class EpisodeOutcome(StrEnum):
    """Coarse label derived from ResolutionAssessment.

    SUCCESS means root cause verified — not merely that the service recovered.
    """

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"


def outcome_from_assessment(assessment: ResolutionAssessment) -> EpisodeOutcome:
    if assessment.root_cause_verified:
        return EpisodeOutcome.SUCCESS
    if assessment.service_recovered or assessment.execution_success:
        return EpisodeOutcome.PARTIAL
    return EpisodeOutcome.FAILURE


class IncidentEpisode(BaseModel):
    """Structured record of a completed incident episode (not just embeddings)."""

    model_config = ConfigDict(extra="forbid")

    episode_id: str
    incident_id: str
    symptoms: list[str] = Field(default_factory=list)
    diagnosis: str = ""
    confirmed_hypothesis: str = ""
    remediation_action: str = ""
    outcome: EpisodeOutcome = EpisodeOutcome.FAILURE
    assessment: ResolutionAssessment = Field(default_factory=ResolutionAssessment)
    evidence: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class MemoryRetrievalResult(BaseModel):
    """A historical episode ranked by symptom similarity."""

    model_config = ConfigDict(extra="forbid")

    episode: IncidentEpisode
    similarity_score: float = Field(ge=0.0, le=1.0)
    matched_symptoms: list[str] = Field(default_factory=list)
