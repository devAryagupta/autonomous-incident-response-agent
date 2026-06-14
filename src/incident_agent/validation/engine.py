from __future__ import annotations

from incident_agent.contracts import FixPlan, ValidationVerdict


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
    _ = fix_plan
    return ValidationVerdict(passed=True, reason="Known remediation pattern")

