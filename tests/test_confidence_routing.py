"""Deterministic tests for confidence conditional routing + replan circuit breaker."""

from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.graph import GRAPH
from incident_agent.pipeline import run_deterministic_lifecycle
from incident_agent.routing import route_on_confidence

_SECRET_LOGS = [
    (
        "Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume \"secret\": "
        "secret \"db-credentials\" not found"
    ),
    "Back-off restarting failed container",
]


def _alert() -> Alert:
    return Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )


def _initial_state(
    *,
    incident_id: str,
    max_replans: int = 2,
    confidence_threshold: float,
) -> IncidentState:
    state = IncidentState(
        incident_id=incident_id,
        created_at=datetime.now(tz=UTC),
        phase="ingest",
        alert=_alert(),
        observations=Observations(logs=list(_SECRET_LOGS), events=[]),
        max_replans=max_replans,
        replan_count=0,
    )
    state.observations.extra["top_n"] = 3
    state.observations.extra["target_ref"] = "deployment/demo-app"
    state.observations.extra["confidence_threshold"] = confidence_threshold
    return state


def test_happy_path_high_confidence_exits_without_replan() -> None:
    """High confidence → END immediately with replan_count == 0."""
    # Threshold 0.0 treats any non-negative score as "high confidence".
    initial = _initial_state(incident_id="inc-happy", confidence_threshold=0.0)

    out = GRAPH.invoke(initial)

    assert out.phase == "done"
    assert out.route == "end"
    assert out.replan_count == 0
    assert out.confidence_score is not None
    assert out.confidence_score >= 0.0
    assert not any(line.startswith("replan:") for line in out.log)


def test_loop_path_low_confidence_increments_replan_once() -> None:
    """Low confidence on first pass → replan once → END with replan_count == 1."""
    # Force low confidence always; max_replans=1 ⇒ exactly one replan then circuit-break.
    initial = _initial_state(
        incident_id="inc-loop",
        max_replans=1,
        confidence_threshold=0.99,
    )

    out = GRAPH.invoke(initial)

    assert out.phase == "done"
    assert out.route == "end"
    assert out.replan_count == 1
    assert any("replan: count=1/1" in line for line in out.log)
    assert out.confidence_score is not None
    assert out.confidence_score < 0.99


def test_max_circuit_breaker_runs_exactly_two_replans() -> None:
    """Low confidence continuously → exactly 2 replans, then END on 3rd evaluation."""
    initial = _initial_state(
        incident_id="inc-breaker",
        max_replans=2,
        confidence_threshold=0.99,
    )

    out = GRAPH.invoke(initial)

    assert out.phase == "done"
    assert out.route == "end"
    assert out.replan_count == 2
    assert out.max_replans == 2
    assert any("replan: count=1/2" in line for line in out.log)
    assert any("replan: count=2/2" in line for line in out.log)
    assert any("max_replans_reached" in line for line in out.log)
    # No third replan entry.
    assert not any("replan: count=3/" in line for line in out.log)


def test_router_decisions_are_deterministic() -> None:
    high = _initial_state(incident_id="r-high", confidence_threshold=0.5)
    high.confidence_score = 0.8
    assert route_on_confidence(high) == "end"

    low_retry = _initial_state(incident_id="r-low", confidence_threshold=0.9)
    low_retry.confidence_score = 0.1
    low_retry.replan_count = 0
    low_retry.max_replans = 2
    assert route_on_confidence(low_retry) == "replan"

    exhausted = _initial_state(incident_id="r-max", confidence_threshold=0.9)
    exhausted.confidence_score = 0.1
    exhausted.replan_count = 2
    exhausted.max_replans = 2
    assert route_on_confidence(exhausted) == "end"


def test_pipeline_and_graph_parity_with_replan_loop() -> None:
    """Both orchestration paths must produce the same final state under looping."""
    created_at = datetime.now(tz=UTC)
    alert = _alert()
    logs = list(_SECRET_LOGS)

    pipeline_state = run_deterministic_lifecycle(
        incident_id="inc-parity-loop",
        alert=alert,
        logs=logs,
        events=[],
        top_n=3,
        target_ref="deployment/demo-app",
        max_replans=2,
        confidence_threshold=0.99,
    )
    pipeline_state.created_at = created_at

    initial = IncidentState(
        incident_id="inc-parity-loop",
        created_at=created_at,
        phase="ingest",
        alert=alert,
        observations=Observations(logs=logs, events=[]),
        max_replans=2,
        replan_count=0,
    )
    initial.observations.extra["top_n"] = 3
    initial.observations.extra["target_ref"] = "deployment/demo-app"
    initial.observations.extra["confidence_threshold"] = 0.99

    graph_state = GRAPH.invoke(initial)
    assert graph_state.model_dump() == pipeline_state.model_dump()
