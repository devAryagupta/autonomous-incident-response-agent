from __future__ import annotations

from incident_agent.contracts import Diagnosis, Evidence, IncidentState


def diagnose(state: IncidentState) -> dict[str, object]:
    """
    Diagnose node (state-in, partial-state-out).

    This is intentionally hardcoded for now to prove the interface
    before LangGraph orchestration is introduced.
    """
    _ = state.alert.model_dump()
    _ = state.observations.logs
    diagnosis = Diagnosis(
        summary="Invalid image tag",
        confidence=0.8,
        evidence=[Evidence(source="logs", text="Deterministic baseline evidence (hardcoded)")],
    )
    return {"diagnosis": diagnosis}

