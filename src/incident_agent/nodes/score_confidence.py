from __future__ import annotations

from incident_agent.contracts import (
    ConfidenceScore,
    FixActionType,
    IncidentState,
)


def score_confidence(
    state: IncidentState,
) -> dict[str, object]:
    """
    Heuristic confidence node (state-in, partial-state-out).

    Heuristic:
    - start with top hypothesis likelihood (0 if none)
    - penalize if plan is noop
    - penalize if validation failed
    - map risk: low no penalty, medium -0.1, high -0.2
    """
    if state.fix_plan is None:
        raise ValueError("state.fix_plan is required before score_confidence()")

    top = state.hypotheses[0].likelihood if state.hypotheses else 0.0
    score = float(top)
    reasons: list[str] = [f"Top hypothesis likelihood={top:.3f}"]

    if any(a.action_type == FixActionType.NOOP for a in state.fix_plan.actions):
        score -= 0.25
        reasons.append("Plan contains noop action (no remediation rule match)")

    if state.validation and not all(v.success for v in state.validation):
        score -= 0.35
        reasons.append("Validation failed")
    elif state.validation:
        reasons.append("Validation passed")

    if state.fix_plan.risk == "medium":
        score -= 0.10
        reasons.append("Risk=medium penalty")
    elif state.fix_plan.risk == "high":
        score -= 0.20
        reasons.append("Risk=high penalty")

    score = max(0.0, min(1.0, score))
    conf = ConfidenceScore(score=round(score, 4), explanation="; ".join(reasons))
    return {"confidence": conf, "confidence_score": conf.score}

