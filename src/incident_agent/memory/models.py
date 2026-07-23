"""Incident memory domain entities (persistence-agnostic)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EpisodeOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"


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
    evidence: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


class MemoryRetrievalResult(BaseModel):
    """A historical episode ranked by symptom similarity."""

    model_config = ConfigDict(extra="forbid")

    episode: IncidentEpisode
    similarity_score: float = Field(ge=0.0, le=1.0)
    matched_symptoms: list[str] = Field(default_factory=list)
