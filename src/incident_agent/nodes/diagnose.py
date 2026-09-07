from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.diagnosis import diagnose_observations


def diagnose(state: IncidentState) -> dict[str, object]:
    """
    One-shot scope classification (state-in, partial-state-out).

    Locks the incident category. Replanning does not call this node again;
    it chooses among causes inside this scope. Does not call LLM,
    Kubernetes, or Prometheus — only inspects observations already on state.
    """
    diagnosis = diagnose_observations(state.observations)
    return {"diagnosis": diagnosis}
