from __future__ import annotations

from incident_agent.contracts import ConfidenceScore, IncidentState


def compute_confidence(
    state: IncidentState,
) -> dict[str, object]:
    """
    Confidence node (state-in, partial-state-out).

    Formula (baseline):
      confidence = top_posterior * validation_score * verification_factor

    Where:
      top_posterior = likelihood after hypothesis verification (Bayesian update)
      validation_score = 1.0 if passed else 0.0
      verification_factor = 1.0 confirmed / 0.75 inconclusive / 0.4 contradicted
    """
    if state.validation_verdict is None:
        raise ValueError("state.validation_verdict is required before compute_confidence()")

    top = state.hypotheses[0] if state.hypotheses else None
    top_p = top.likelihood if top else 0.0
    validation_score = 1.0 if state.validation_verdict.passed else 0.0

    verification_factor = 0.75
    verification_result = "none"
    if top is not None and state.hypothesis_verifications:
        match = next(
            (v for v in state.hypothesis_verifications if v.hypothesis_id == top.hypothesis_id),
            None,
        )
        if match is not None:
            verification_result = match.result
            if match.result == "confirmed":
                verification_factor = 1.0
            elif match.result == "contradicted":
                verification_factor = 0.4
            else:
                verification_factor = 0.75

    score = round(float(top_p) * validation_score * verification_factor, 4)
    explanation = (
        f"top_posterior={top_p:.3f} * validation_score={validation_score:.1f} "
        f"* verification_factor={verification_factor:.2f} "
        f"(verification={verification_result}; {state.validation_verdict.reason})"
    )
    conf = ConfidenceScore(score=score, explanation=explanation)
    return {"confidence": conf, "confidence_score": conf.score}

