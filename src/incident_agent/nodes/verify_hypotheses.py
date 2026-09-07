from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.diagnosis import assess_diagnosis_scope
from incident_agent.verification import verify_hypotheses_from_state
from incident_agent.verification.conclusion import (
    chosen_confirmed_hypothesis_id,
    summarize_cause_resolution,
)


def verify_hypotheses(state: IncidentState) -> dict[str, object]:
    """
    Live-graph verification node (state-in, partial-state-out).

    Uses ``verification.engine`` only: regex specs → Bayes-factor buckets →
    posterior belief on ``Hypothesis.likelihood``. Does not call the
    standalone OOM loop (``verification.loop`` / ``HypothesisState``).
    """
    verifications, updated = verify_hypotheses_from_state(state)
    log = list(state.log)
    confirmed = sum(1 for v in verifications if v.result == "confirmed")
    contradicted = sum(1 for v in verifications if v.result == "contradicted")
    chosen_id = chosen_confirmed_hypothesis_id(updated, verifications)
    top_name = updated[0].description if updated else "<none>"
    top_belief = updated[0].belief if updated else 0.0
    log.append(
        f"verify_hypotheses: confirmed={confirmed} contradicted={contradicted} "
        f"top={top_name} belief={top_belief:.3f} chosen={chosen_id or '<none>'}"
    )
    updates: dict[str, object] = {
        "hypothesis_verifications": verifications,
        "hypotheses": updated,
        "chosen_hypothesis_id": chosen_id,
        "log": log,
    }
    if state.diagnosis is not None:
        diagnosis = assess_diagnosis_scope(state.diagnosis, state.observations)
        updates["diagnosis"] = diagnosis
        if not diagnosis.scope_valid:
            log.append(
                f"verify_hypotheses: diagnosis_scope_invalid "
                f"{diagnosis.scope_invalid_reason}"
            )
        else:
            resolution = summarize_cause_resolution(
                state,
                diagnosis=diagnosis,
                verifications=verifications,
            )
            log.append(f"verify_hypotheses: {resolution}")
        updates["log"] = log
    return updates
