from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.verification import verify_hypotheses_from_state


def verify_hypotheses(state: IncidentState) -> dict[str, object]:
    """
    Hypothesis verification node (state-in, partial-state-out).

    Challenges each hypothesis against observed evidence, applies a Bayesian-style
    update, and re-ranks hypotheses by posterior likelihood.
    """
    verifications, updated = verify_hypotheses_from_state(state)
    log = list(state.log)
    confirmed = sum(1 for v in verifications if v.result == "confirmed")
    contradicted = sum(1 for v in verifications if v.result == "contradicted")
    top_name = updated[0].description if updated else "<none>"
    top_p = updated[0].likelihood if updated else 0.0
    log.append(
        f"verify_hypotheses: confirmed={confirmed} contradicted={contradicted} "
        f"top={top_name} p={top_p:.3f}"
    )
    return {
        "hypothesis_verifications": verifications,
        "hypotheses": updated,
        "chosen_hypothesis_id": updated[0].hypothesis_id if updated else None,
        "log": log,
    }
