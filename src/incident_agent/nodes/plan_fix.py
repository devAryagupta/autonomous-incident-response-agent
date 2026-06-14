from __future__ import annotations

from incident_agent.contracts import FixPlan, Hypothesis
from incident_agent.remediation import CatalogMatch, plan_from_hypotheses


def plan_fix(
    *,
    hypotheses: list[Hypothesis],
    target_ref: str = "<workload>",
) -> tuple[FixPlan, CatalogMatch]:
    """
    Deterministic fix planner.

    Input: ranked hypotheses
    Output: a FixPlan + metadata about which remediation rule matched.
    """
    return plan_from_hypotheses(hypotheses=hypotheses, target_ref=target_ref)

