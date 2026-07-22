from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.graph import GRAPH
from incident_agent.pipeline import run_deterministic_lifecycle


def _same_initial_state() -> tuple[Alert, list[str], list[str], IncidentState]:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    logs = [
        (
            "Warning  FailedMount  kubelet  MountVolume.SetUp failed for volume \"secret\": "
            "secret \"db-credentials\" not found"
        ),
        "Back-off restarting failed container",
    ]
    events: list[str] = []

    # Shared created_at so pipeline/graph outputs can be compared bit-for-bit.
    created_at = datetime.now(tz=UTC)
    initial = IncidentState(
        incident_id="inc-graph-1",
        created_at=created_at,
        phase="ingest",
        alert=alert,
        observations=Observations(logs=list(logs), events=list(events)),
    )
    initial.observations.extra["top_n"] = 3
    initial.observations.extra["target_ref"] = "deployment/demo-app"
    # Keep this parity test on the single-pass happy path (no replan loop).
    initial.observations.extra["confidence_threshold"] = 0.0
    return alert, logs, events, initial


def test_graph_invoke_matches_pipeline_output() -> None:
    alert, logs, events, initial = _same_initial_state()

    pipeline_state = run_deterministic_lifecycle(
        incident_id=initial.incident_id,
        alert=alert,
        logs=logs,
        events=events,
        top_n=3,
        target_ref="deployment/demo-app",
        confidence_threshold=0.0,
        created_at=initial.created_at,
    )

    graph_state = GRAPH.invoke(initial)

    assert graph_state.model_dump() == pipeline_state.model_dump()


def test_graph_topology_has_conditional_replan() -> None:
    """Topology: enrich → diagnose → … → confidence ⇄ replan; confidence → finalize → END."""
    app = GRAPH._app
    nodes = set(getattr(app, "nodes", {}) or {})
    if not nodes and hasattr(app, "get_graph"):
        nodes = {n for n in app.get_graph().nodes if n not in {"__start__", "__end__"}}

    assert "enrich" in nodes
    assert "diagnose" in nodes
    assert "hypothesize" in nodes
    assert "verify_hypotheses" in nodes
    assert "plan_fix" in nodes
    assert "validate_fix" in nodes
    assert "confidence" in nodes
    assert "replan" in nodes
    assert "finalize" in nodes

