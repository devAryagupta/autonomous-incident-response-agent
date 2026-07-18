from __future__ import annotations

from incident_agent.contracts import FixActionType, FixPlan, ValidationVerdict


def validate_plan(*, fix_plan: FixPlan) -> ValidationVerdict:
    """
    Validation Engine (interface-only, fake implementation).

    Today:
    - returns a deterministic verdict to prove the interface.

    Later:
    - kubectl --dry-run validation
    - staging validation
    - simulation
    """
    if fix_plan.risk is None:
        return ValidationVerdict(passed=False, reason="Risk not assigned")

    if not fix_plan.actions:
        return ValidationVerdict(passed=False, reason="FixPlan has no actions")

    supported = set(FixActionType)
    for a in fix_plan.actions:
        if a.action_type not in supported:
            return ValidationVerdict(passed=False, reason=f"Unsupported action: {a.action_type}")
        if not a.target:
            return ValidationVerdict(passed=False, reason="Missing target")
        if not a.rationale:
            return ValidationVerdict(passed=False, reason="Missing rationale")

    top = fix_plan.actions[0].action_type
    return ValidationVerdict(passed=True, reason=f"{top.value} supported")

