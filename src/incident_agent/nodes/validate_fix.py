from __future__ import annotations

from incident_agent.contracts import IncidentState, ValidationResult
from incident_agent.validation import validate_plan


def validate_fix(state: IncidentState) -> dict[str, object]:
    """
    Validate-fix node (state-in, partial-state-out).

    No Kubernetes calls yet. We only perform structural validation:
    - plan must contain at least one action
    - each action must have rationale and target_ref
    """
    if state.fix_plan is None:
        raise ValueError("state.fix_plan is required before validate_fix()")

    fix_plan = state.fix_plan

    if not fix_plan.actions:
        validation = [
            ValidationResult(
                method="simulation",
                success=False,
                diff=None,
                notes="FixPlan has no actions",
            )
        ]
        return {"validation": validation, "validation_verdict": validate_plan(fix_plan=fix_plan)}

    for a in fix_plan.actions:
        if not a.rationale or not a.target:
            validation = [
                ValidationResult(
                    method="simulation",
                    success=False,
                    diff=None,
                    notes="FixAction missing rationale or target_ref",
                )
            ]
            return {"validation": validation, "validation_verdict": validate_plan(fix_plan=fix_plan)}

    validation = [
        ValidationResult(
            method="simulation",
            success=True,
            diff=None,
            notes="Structural validation passed (no side effects executed).",
        )
    ]
    return {"validation": validation, "validation_verdict": validate_plan(fix_plan=fix_plan)}

