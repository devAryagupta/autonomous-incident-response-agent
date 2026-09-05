"""HypothesisState for the standalone OOM Bayesian loop only.

Not the LangGraph payload. The live graph uses ``contracts.Hypothesis``.
Do not convert these in ``verify_hypotheses`` — that node never sees this type.
See docs/VERIFICATION_STACKS.md.
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
    """Prior → posterior belief for one root-cause hypothesis.

    Used by the standalone OOM Bayesian loop (``verification/loop.py``), not
    as the LangGraph ``IncidentState`` payload. The live graph stores the same
    idea on ``contracts.Hypothesis.likelihood`` (prior, then posterior).
    """

    id: str
    description: str
    prior_probability: float = Field(ge=0.0, le=1.0) # prior probability means the probability of the hypothesis before the evidence is considered.
    posterior_probability: float = Field(ge=0.0, le=1.0) # posterior probability means the probability of the hypothesis after the evidence is considered.
    status: HypothesisStatus = HypothesisStatus.UNVERIFIED
    supporting_evidence: list[str] = Field(default_factory=list)
