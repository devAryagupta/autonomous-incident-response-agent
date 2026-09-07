from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.remediation.decision import decide_remediation


def plan_fix(
    state: IncidentState,
) -> dict[str, object]:
    """
    Plan-fix node (state-in, partial-state-out).

    Runs the Remediation Decision Engine: generate ranked RemediationOptions
    (heuristic suitability + safety), choose the minimum effective safe action,
    and materialize a FixPlan for validation. Suitability is not P(success).
    Does not execute.
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before plan_fix()")

    decision = decide_remediation(state)
    log = list(state.log)
    if decision.chosen is not None:
        log.append(
            "plan_fix: "
            f"chosen={decision.chosen.action} "
            f"blast_radius={decision.chosen.blast_radius} "
            f"reversibility={decision.chosen.reversibility} "
            f"rollback_possible={decision.chosen.rollback_possible} "
            f"safety_score={decision.chosen.safety_score:.3f} "
            f"rule={decision.matched_rule}"
        )
    else:
        log.append("plan_fix: matched_rule=<none>")

    return {
        "remediation_options": decision.options,
        "chosen_remediation_id": decision.chosen.option_id if decision.chosen else None,
        "chosen_hypothesis_id": (
            decision.chosen.hypothesis_id
            if decision.chosen and decision.chosen.hypothesis_id
            else state.chosen_hypothesis_id
        ),
        "fix_plan": decision.fix_plan,
        "log": log,
    }
