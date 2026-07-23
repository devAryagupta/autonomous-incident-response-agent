"""Bayesian-loop evidence schemas (telemetry curiosity + results).

These models are the hexagonal port for metric/log/event evidence used by the
Bayesian verification loop. Pipeline contracts (`contracts.EvidenceRequest`)
remain the Stage-0 graph payload; adapt at the provider boundary.
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
