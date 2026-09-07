from __future__ import annotations

from typing import Any

from incident_agent.contracts import IncidentState
from incident_agent.execution.outcome import verify_execution_outcome
from incident_agent.providers import ProviderBundle, resolve_providers


def verify_outcome(
    state: IncidentState,
    *,
    providers: ProviderBundle | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """
    Post-execution verification.

    Re-collects evidence and checks ExecutionPlan.expected_outcome.
    Never treats command success alone as incident resolution.
    ``incident_resolved`` is service recovery; see assessment for cause.
    """
    bundle = providers or resolve_providers(config)
    outcome, observations = verify_execution_outcome(state, providers=bundle)
    flags = outcome.assessment
    log = list(state.log)
    log.append(
        "verify_outcome: "
        f"resolved={outcome.resolved} "
        f"observed={len(outcome.observed_outcome)}/{len(outcome.expected_outcome)} "
        f"execution_success={flags.execution_success} "
        f"service_recovered={flags.service_recovered} "
        f"stable_recovery={flags.stable_recovery} "
        f"root_cause_verified={flags.root_cause_verified} "
        f"reason={outcome.reason}"
    )
    return {
        "outcome_verification": outcome,
        "incident_resolved": outcome.resolved,
        "observations": observations,
        "log": log,
    }
