from __future__ import annotations

from incident_agent.contracts import IncidentState
from incident_agent.hypothesis import hypothesize_from_state


def hypothesize(state: IncidentState) -> dict[str, object]:
    """
    Hypothesize node (state-in, partial-state-out).

    Delegates to the deterministic hypothesis engine. Diagnosis names the
    symptom; hypotheses explain ranked underlying causes with verification hints.
    """
    hypotheses = hypothesize_from_state(state)
    return {"hypotheses": hypotheses}
