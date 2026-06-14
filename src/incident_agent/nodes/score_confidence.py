from __future__ import annotations

from incident_agent.contracts import ConfidenceScore, FixPlan, Hypothesis, ValidationResult


def score_confidence(
    *,
    hypotheses: list[Hypothesis],
    fix_plan: FixPlan,
    validation: list[ValidationResult],
) -> ConfidenceScore:
    """
    Deterministic confidence scoring baseline (no metrics, no LLM).

    Heuristic:
    - start with top hypothesis likelihood (0 if none)
    - penalize if plan is noop
    - penalize if validation failed
    - map risk: low no penalty, medium -0.1, high -0.2
    """
    top = hypotheses[0].likelihood if hypotheses else 0.0
    score = float(top)
    reasons: list[str] = [f"Top hypothesis likelihood={top:.3f}"]

    if any(a.kind == "noop" for a in fix_plan.actions):
        score -= 0.25
        reasons.append("Plan contains noop action (no remediation rule match)")

    if validation and not all(v.success for v in validation):
        score -= 0.35
        reasons.append("Validation failed")
    elif validation:
        reasons.append("Validation passed")

    if fix_plan.risk == "medium":
        score -= 0.10
        reasons.append("Risk=medium penalty")
    elif fix_plan.risk == "high":
        score -= 0.20
        reasons.append("Risk=high penalty")

    score = max(0.0, min(1.0, score))
    return ConfidenceScore(score=round(score, 4), explanation="; ".join(reasons))

