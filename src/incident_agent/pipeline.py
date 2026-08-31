from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    IncidentState,
    Observations,
)
from incident_agent.nodes import (
    approve,
    collect_evidence,
    compute_confidence,
    diagnose,
    execute_fix,
    hypothesize,
    plan_fix,
    pre_execute_validate,
    prepare_execution,
    validate_fix,
    verify_hypotheses,
    verify_outcome,
)
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import ProviderBundle, default_providers
from incident_agent.routing import bump_replan, finalize, route_on_confidence


def _apply(state: IncidentState, updates: dict[str, object]) -> None:
    for k, v in updates.items():
        setattr(state, k, v)


def _run_plan_validate_score(state: IncidentState, *, providers: ProviderBundle) -> None:
    state.phase = "hypothesize"
    _apply(state, hypothesize(state))

    state.phase = "collect_evidence"
    _apply(state, collect_evidence(state, providers=providers))

    state.phase = "verify_hypotheses"
    _apply(state, verify_hypotheses(state))

    state.phase = "plan_fix"
    _apply(state, plan_fix(state))

    state.phase = "validate_fix"
    _apply(state, validate_fix(state))

    state.phase = "score_confidence"
    _apply(state, compute_confidence(state))


def _run_execution_lifecycle(state: IncidentState, *, providers: ProviderBundle) -> None:
    state.phase = "prepare_execution"
    _apply(state, prepare_execution(state))

    state.phase = "pre_execute_validate"
    _apply(state, pre_execute_validate(state))

    state.phase = "approve"
    _apply(state, approve(state))

    state.phase = "execute"
    _apply(state, execute_fix(state, providers=providers))

    state.phase = "verify_outcome"
    _apply(state, verify_outcome(state, providers=providers))


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
    stop_before_execution: bool = False,
) -> IncidentState:
    """
    Execute the deterministic incident lifecycle (no LangGraph, no LLM, no live I/O).

    Providers are injected the same way as GRAPH (defaults = synthetic/dry-run/no-memory).

    When ``stop_before_execution`` is True, stop after plan / validate / confidence
    (partial eval slice — no dry-run execute or outcome verify).
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
        _run_plan_validate_score(state, providers=bundle)
        decision = route_on_confidence(state)
        if decision == "replan":
            _apply(state, bump_replan(state))
            continue
        if stop_before_execution:
            state.phase = "score_confidence"
            break
        _run_execution_lifecycle(state, providers=bundle)
        _apply(state, finalize(state, providers=bundle))
        break

    return state
