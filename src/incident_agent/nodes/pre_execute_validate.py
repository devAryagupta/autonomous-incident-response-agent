from __future__ import annotations

from typing import Any

from incident_agent.contracts import IncidentState, ValidationResult
from incident_agent.execution import (
    check_preconditions,
    execution_namespace,
    target_probe_ref,
)
from incident_agent.providers import ProviderBundle, resolve_providers


def pre_execute_validate(
    state: IncidentState,
    *,
    providers: ProviderBundle | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """
    Pre-execution validation of ExecutionPlan preconditions.

    State-shaped checks stay in ``check_preconditions``. Target existence goes
    through ExecutionProvider.resource_exists (DryRun infers; kubectl GETs).
    """
    if state.execution_plan is None:
        raise ValueError("state.execution_plan is required before pre_execute_validate()")

    bundle = providers or resolve_providers(config)
    kind, name = target_probe_ref(state.execution_plan)
    namespace = execution_namespace(state)
    target_exists = bundle.execution.resource_exists(kind, namespace, name)
    verdict = check_preconditions(state, target_exists=target_exists)

    validation = list(state.validation)
    validation.append(
        ValidationResult(
            method="simulation",
            success=verdict.passed,
            diff=None,
            notes=(
                f"pre_execute: {verdict.reason} "
                f"(target={kind}/{name} ns={namespace} exists={target_exists})"
            ),
        )
    )
    log = list(state.log)
    log.append(
        f"pre_execute_validate: passed={verdict.passed} reason={verdict.reason} "
        f"target={kind}/{name} ns={namespace} exists={target_exists}"
    )
    return {
        "validation": validation,
        "validation_verdict": verdict,
        "log": log,
    }
