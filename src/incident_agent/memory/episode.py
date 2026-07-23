"""Build IncidentEpisode records from completed IncidentState."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from incident_agent.contracts import IncidentState
from incident_agent.memory.models import EpisodeOutcome, IncidentEpisode
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


def _confirmed_hypothesis(state: IncidentState) -> str:
    for v in state.hypothesis_verifications:
        if v.result == "confirmed":
            return v.hypothesis
    if state.hypotheses:
        chosen = state.chosen_hypothesis_id
        if chosen:
            for hyp in state.hypotheses:
                if hyp.hypothesis_id == chosen:
                    return hyp.description
        return state.hypotheses[0].description
    return ""


def _remediation_action(state: IncidentState) -> str:
    if state.execution and state.execution.action:
        return state.execution.action
    if state.execution_plan and state.execution_plan.action:
        return state.execution_plan.action
    if state.chosen_remediation_id:
        for opt in state.remediation_options:
            if opt.option_id == state.chosen_remediation_id:
                return opt.action
    return ""


def _outcome(state: IncidentState) -> EpisodeOutcome:
    if state.incident_resolved is True:
        return EpisodeOutcome.SUCCESS
    if state.execution is not None and state.execution.success:
        return EpisodeOutcome.PARTIAL
    return EpisodeOutcome.FAILURE


def episode_from_state(state: IncidentState) -> IncidentEpisode | None:
    """
    Convert a finished incident into a structured memory episode.

    Returns None when there is not enough signal to learn from.
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

    confirmed = _confirmed_hypothesis(state)
    action = _remediation_action(state)
    if not symptoms and not diagnosis:
        return None

    stamp = state.created_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)

    return IncidentEpisode(
        episode_id=str(uuid.uuid4()),
        incident_id=state.incident_id,
        symptoms=symptoms,
        diagnosis=diagnosis,
        confirmed_hypothesis=confirmed,
        remediation_action=action,
        outcome=_outcome(state),
        evidence=_evidence_blob(state),
        timestamp=stamp if isinstance(stamp, datetime) else datetime.now(tz=UTC),
    )
