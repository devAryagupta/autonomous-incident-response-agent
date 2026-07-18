from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.remediation import plan_from_hypotheses


def plan_fix(
    state: IncidentState,
) -> dict[str, object]:
    """
    Plan-fix node (state-in, partial-state-out).
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before plan_fix()")

    target_ref = str(state.observations.extra.get("target_ref", "<workload>"))
    fix_plan, match = plan_from_hypotheses(hypotheses=state.hypotheses, target_ref=target_ref)
    log = list(state.log)
    if match.matched_rule:
        log.append(f"plan_fix: matched_rule={match.matched_rule}")
    else:
        log.append("plan_fix: matched_rule=<none>")
    return {"fix_plan": fix_plan, "log": log}

