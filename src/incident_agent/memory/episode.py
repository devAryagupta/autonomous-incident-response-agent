"""Build IncidentEpisode records from completed IncidentState."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from incident_agent.contracts import IncidentState
from incident_agent.memory.resolution import (
    applied_action,
    assess_resolution_from_state,
    confirmed_cause,
)
from incident_agent.memory.models import IncidentEpisode, outcome_from_assessment
from incident_agent.memory.retrieval import extract_symptoms_from_text


def _evidence_blob(state: IncidentState) -> dict[str, Any]:
    diagnosis_evidence = []
    if state.diagnosis is not None:
        diagnosis_evidence = [e.text for e in state.diagnosis.evidence]
    hyp_evidence: list[str] = []
    for hyp in state.hypotheses[:3]:
        hyp_evidence.extend(e.text for e in hyp.evidence[:2])
    return {
        "diagnosis_evidence": diagnosis_evidence,
        "hypothesis_evidence": hyp_evidence,
        "collected_summaries": [
            r.summary for r in state.evidence_results if r.summary
        ][:10],
        "verification": [
            {
                "hypothesis": v.hypothesis,
                "result": v.result,
                "observed": v.observed_evidence,
            }
            for v in state.hypothesis_verifications
        ],
        "outcome_verification": (
            state.outcome_verification.model_dump(mode="json")
            if state.outcome_verification
            else None
        ),
    }


def episode_from_state(state: IncidentState) -> IncidentEpisode | None:
    """
    Convert a finished incident into a structured memory episode.

    Returns None when there is not enough signal to learn from.
    SUCCESS is reserved for root-cause verification, not service recovery.
    """
    diagnosis = ""
    if state.diagnosis is not None:
        diagnosis = state.diagnosis.category or state.diagnosis.summary

    symptoms = extract_symptoms_from_text(
        state.alert.alert_name,
        diagnosis,
        *state.observations.logs,
        *state.observations.events,
    )
    if state.alert.alert_name and state.alert.alert_name not in symptoms:
        symptoms.insert(0, state.alert.alert_name)

    confirmed = confirmed_cause(state)
    action = applied_action(state)
    if not symptoms and not diagnosis:
        return None

    stamp = state.created_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)

    assessment = assess_resolution_from_state(state)
    return IncidentEpisode(
        episode_id=str(uuid.uuid4()),
        incident_id=state.incident_id,
        symptoms=symptoms,
        diagnosis=diagnosis,
        confirmed_hypothesis=confirmed,
        remediation_action=action,
        outcome=outcome_from_assessment(assessment),
        assessment=assessment,
        evidence=_evidence_blob(state),
        timestamp=stamp if isinstance(stamp, datetime) else datetime.now(tz=UTC),
    )
