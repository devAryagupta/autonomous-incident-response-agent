from __future__ import annotations

from incident_agent.contracts import Alert, Diagnosis


def diagnose(alert: Alert, logs: list[str]) -> Diagnosis:
    """
    Diagnose an incident from alert + logs.

    This is intentionally hardcoded for now to prove the interface
    before LangGraph orchestration is introduced.
    """
    _ = alert.model_dump()
    _ = logs
    return Diagnosis(
        summary="Invalid image tag",
        confidence=0.8,
        evidence=[
            {
                "schema_version": "1",
                "source": "logs",
                "text": "Synthetic placeholder evidence (hardcoded)",
            }
        ],
    )

