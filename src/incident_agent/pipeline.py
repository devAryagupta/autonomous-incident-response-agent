from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    IncidentState,
    Observations,
)
from incident_agent.nodes import compute_confidence, diagnose, hypothesize, plan_fix, validate_fix
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import ProviderBundle, default_providers
from incident_agent.routing import bump_replan, finalize, route_on_confidence


def _apply(state: IncidentState, updates: dict[str, object]) -> None:
    for k, v in updates.items():
        setattr(state, k, v)


def _run_plan_validate_score(state: IncidentState) -> None:
    state.phase = "hypothesize"
    _apply(state, hypothesize(state))

    state.phase = "plan_fix"
    _apply(state, plan_fix(state))

    state.phase = "validate_fix"
    _apply(state, validate_fix(state))

    state.phase = "score_confidence"
    _apply(state, compute_confidence(state))


def run_deterministic_lifecycle(
    *,
    incident_id: str,
    alert: Alert,
    logs: list[str],
    events: list[str] | None = None,
    top_n: int = 3,
    target_ref: str = "<workload>",
    max_replans: int | None = None,
    confidence_threshold: float | None = None,
    providers: ProviderBundle | None = None,
    created_at: datetime | None = None,
) -> IncidentState:
    """
    Execute the deterministic incident lifecycle (no LangGraph, no LLM, no live I/O).

    Providers are injected the same way as GRAPH (defaults = synthetic/dry-run/no-memory).
    """
    bundle = providers or default_providers()

    now = created_at or datetime.now(tz=UTC)
    state = IncidentState(
        incident_id=incident_id,
        created_at=now,
        phase="ingest",
        alert=alert,
        observations=Observations(logs=list(logs), events=list(events or [])),
    )
    # config values required by state-based nodes / routing
    state.observations.extra["top_n"] = int(top_n)
    state.observations.extra["target_ref"] = str(target_ref)
    if confidence_threshold is not None:
        state.observations.extra["confidence_threshold"] = float(confidence_threshold)
    if max_replans is not None:
        state.max_replans = int(max_replans)

    _apply(state, enrich(state, providers=bundle))

    state.phase = "diagnose"
    _apply(state, diagnose(state))

    while True:
        _run_plan_validate_score(state)
        decision = route_on_confidence(state)
        if decision == "replan":
            _apply(state, bump_replan(state))
            continue
        _apply(state, finalize(state, providers=bundle))
        break

    return state
