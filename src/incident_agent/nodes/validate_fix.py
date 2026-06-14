from __future__ import annotations

from incident_agent.contracts import FixPlan, ValidationResult


def validate_fix(*, fix_plan: FixPlan) -> list[ValidationResult]:
    """
    Deterministic "shadow validation" baseline.

    No Kubernetes calls yet. We only perform structural validation:
    - plan must contain at least one action
    - each action must have rationale and target_ref
    """
    if not fix_plan.actions:
        return [
            ValidationResult(
                method="simulation",
                success=False,
                diff=None,
                notes="FixPlan has no actions",
            )
        ]

    for a in fix_plan.actions:
        if not a.rationale or not a.target_ref:
            return [
                ValidationResult(
                    method="simulation",
                    success=False,
                    diff=None,
                    notes="FixAction missing rationale or target_ref",
                )
            ]

    return [
        ValidationResult(
            method="simulation",
            success=True,
            diff=None,
            notes="Structural validation passed (no side effects executed).",
        )
    ]

