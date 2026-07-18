from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    IncidentState,
    Observations,
)
from incident_agent.nodes import compute_confidence, diagnose, hypothesize, plan_fix, validate_fix


def _apply(state: IncidentState, updates: dict[str, object]) -> None:
    for k, v in updates.items():
        setattr(state, k, v)


def run_deterministic_lifecycle(
    *,
    incident_id: str,
    alert: Alert,
    logs: list[str],
    events: list[str] | None = None,
    top_n: int = 3,
    target_ref: str = "<workload>",
) -> IncidentState:
    """
    Execute the complete deterministic incident lifecycle (no LangGraph, no LLM, no I/O).

    Flow:
      Incident -> Diagnose -> Hypothesize -> Plan Fix -> Validate Fix -> Score Confidence
    """
    now = datetime.now(tz=UTC)
    state = IncidentState(
        incident_id=incident_id,
        created_at=now,
        phase="ingest",
        alert=alert,
        observations=Observations(logs=list(logs), events=list(events or [])),
    )
    # config values required by state-based nodes
    state.observations.extra["top_n"] = int(top_n)
    state.observations.extra["target_ref"] = str(target_ref)

    state.phase = "diagnose"
    _apply(state, diagnose(state))

    state.phase = "hypothesize"
    _apply(state, hypothesize(state))

    state.phase = "plan_fix"
    _apply(state, plan_fix(state))

    state.phase = "validate_fix"
    _apply(state, validate_fix(state))

    state.phase = "score_confidence"
    _apply(state, compute_confidence(state))

    state.phase = "done"
    return state

