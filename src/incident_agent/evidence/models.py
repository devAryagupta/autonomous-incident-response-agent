"""Telemetry schemas for the standalone OOM loop only.

``EvidenceRequest`` / ``TelemetryResult`` here are not
``contracts.EvidenceRequest`` / ``EvidenceResult``. The live graph collect/
verify path uses contracts. Adapt at a provider boundary if a future stage
wires this loop into IncidentState. See docs/VERIFICATION_STACKS.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EvidenceModelBase(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class EvidenceType(StrEnum):
    METRIC = "METRIC"
    LOG = "LOG"
    EVENT = "EVENT"


class EvidenceRequest(EvidenceModelBase):
    """What the Bayesian loop needs next from a telemetry provider."""

    type: EvidenceType
    query: str
    target: str
    time_range: str = "30m"


class TelemetryResult(EvidenceModelBase):
    """Numeric series returned for one EvidenceRequest (typically Prometheus)."""

    request: EvidenceRequest
    datapoints: list[float] = Field(default_factory=list)
    timestamps: list[datetime] | None = None
