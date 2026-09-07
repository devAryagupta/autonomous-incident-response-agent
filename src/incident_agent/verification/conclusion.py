"""Symptom certainty vs root-cause certainty.

Diagnosis names the incident family. ``HypothesisVerification.result`` says
whether a competing cause was confirmed. Those are different questions.
"""

from __future__ import annotations

from incident_agent.contracts import (
    Diagnosis,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
)
from incident_agent.diagnosis.scope import diagnosis_scope_family


def confirmed_verifications(
    verifications: list[HypothesisVerification],
) -> list[HypothesisVerification]:
    return [item for item in verifications if item.result == "confirmed"]


def chosen_confirmed_hypothesis_id(
    hypotheses: list[Hypothesis],
    verifications: list[HypothesisVerification],
) -> str | None:
    """Highest-belief *confirmed* cause, or None when no cause is confirmed."""
    confirmed = {item.hypothesis_id for item in confirmed_verifications(verifications)}
    if not confirmed:
        return None
    for hyp in hypotheses:
        if hyp.hypothesis_id in confirmed:
            return hyp.hypothesis_id
    return None


def summarize_cause_resolution(
    state: IncidentState,
    *,
    diagnosis: Diagnosis | None = None,
    verifications: list[HypothesisVerification] | None = None,
) -> str:
    """One line: family confirmed or not, and whether a cause was distinguished."""
    diagnosis = state.diagnosis if diagnosis is None else diagnosis
    items = state.hypothesis_verifications if verifications is None else verifications
    if diagnosis is None:
        return "Incident family not confirmed"
    family = diagnosis.category or diagnosis.summary or "Unknown"
    confirmed = confirmed_verifications(items)
    if not items:
        return f"{family} scoped; root causes not yet verified"
    if not confirmed:
        if diagnosis_scope_family(diagnosis.category) == "oom":
            return "OOM confirmed, root cause not yet distinguishable"
        return f"{family} confirmed, root cause not yet distinguishable"
    return f"{family} confirmed, root cause={confirmed[0].hypothesis}"
