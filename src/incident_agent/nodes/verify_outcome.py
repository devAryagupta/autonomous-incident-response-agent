from __future__ import annotations

from typing import Any

from incident_agent.contracts import IncidentState
from incident_agent.execution import verify_execution_outcome
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
    """
    bundle = providers or resolve_providers(config)
    outcome, observations = verify_execution_outcome(state, providers=bundle)
    log = list(state.log)
    log.append(
        "verify_outcome: "
        f"resolved={outcome.resolved} "
        f"observed={len(outcome.observed_outcome)}/{len(outcome.expected_outcome)} "
        f"reason={outcome.reason}"
    )
    return {
        "outcome_verification": outcome,
        "incident_resolved": outcome.resolved,
        "observations": observations,
        "log": log,
    }
