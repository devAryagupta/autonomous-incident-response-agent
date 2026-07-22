from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.diagnosis import diagnose_observations


def diagnose(state: IncidentState) -> dict[str, object]:
    """
    Diagnose node (state-in, partial-state-out).

    Delegates to the deterministic diagnosis engine. Does not call LLM,
    Kubernetes, or Prometheus — only inspects observations already on state.
    """
    diagnosis = diagnose_observations(state.observations)
    return {"diagnosis": diagnosis}
