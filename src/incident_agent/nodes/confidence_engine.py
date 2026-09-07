from __future__ import annotations

from incident_agent.contracts import ConfidenceScore, IncidentState


def _top_verification_result(state: IncidentState) -> str:
    top = state.hypotheses[0] if state.hypotheses else None
    if top is None or not state.hypothesis_verifications:
        return "none"
    match = next(
        (v for v in state.hypothesis_verifications if v.hypothesis_id == top.hypothesis_id),
        None,
    )
    return match.result if match is not None else "none"


def compute_confidence(
    state: IncidentState,
) -> dict[str, object]:
    """
    Routing / gate score (state-in, partial-state-out).

    This is a heuristic used to replan vs enter execution. It is not a
    probability of success and not interchangeable with hypothesis belief.

    Formula:
      gate_score = top_belief * validation_score

    ``top_belief`` is already the posterior. The verification verdict that
    produced that posterior is not multiplied again.
    ``validation_score`` is 1.0 if the plan passed structural validation else 0.0.
    """
    if state.validation_verdict is None:
        raise ValueError("state.validation_verdict is required before compute_confidence()")

    top = state.hypotheses[0] if state.hypotheses else None
    top_belief = top.belief if top else 0.0
    validation_score = 1.0 if state.validation_verdict.passed else 0.0
    verification_result = _top_verification_result(state)

    score = round(float(top_belief) * validation_score, 4)
    explanation = (
        f"top_belief={top_belief:.3f} * validation_score={validation_score:.1f} "
        f"(verification={verification_result} already in posterior; "
        f"{state.validation_verdict.reason})"
    )
    conf = ConfidenceScore(score=score, explanation=explanation)
    return {"confidence": conf, "confidence_score": conf.score}

