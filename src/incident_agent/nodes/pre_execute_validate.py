from __future__ import annotations

from incident_agent.contracts import IncidentState, ValidationResult
from incident_agent.execution import check_preconditions


def pre_execute_validate(state: IncidentState) -> dict[str, object]:
    """Pre-execution validation of ExecutionPlan preconditions (no live I/O)."""
    if state.execution_plan is None:
        raise ValueError("state.execution_plan is required before pre_execute_validate()")

    verdict = check_preconditions(state)
    validation = list(state.validation)
    validation.append(
        ValidationResult(
            method="simulation",
            success=verdict.passed,
            diff=None,
            notes=f"pre_execute: {verdict.reason}",
        )
    )
    log = list(state.log)
    log.append(
        f"pre_execute_validate: passed={verdict.passed} reason={verdict.reason}"
    )
    return {
        "validation": validation,
        "validation_verdict": verdict,
        "log": log,
    }
