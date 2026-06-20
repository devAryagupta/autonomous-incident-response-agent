from __future__ import annotations

from incident_agent.contracts import ConfidenceScore, IncidentState


def compute_confidence(
    state: IncidentState,
) -> dict[str, object]:
    """
    Confidence node (state-in, partial-state-out).

    Formula (baseline):
      confidence = top_hypothesis_probability * validation_score

    Where:
      validation_score = 1.0 if passed else 0.0
    """
    if state.validation_verdict is None:
        raise ValueError("state.validation_verdict is required before compute_confidence()")

    top_p = state.hypotheses[0].likelihood if state.hypotheses else 0.0
    validation_score = 1.0 if state.validation_verdict.passed else 0.0
    score = round(float(top_p) * validation_score, 4)
    explanation = (
        f"top_hypothesis_probability={top_p:.3f} * validation_score={validation_score:.1f} "
        f"({state.validation_verdict.reason})"
    )
    conf = ConfidenceScore(score=score, explanation=explanation)
    return {"confidence": conf, "confidence_score": conf.score}

