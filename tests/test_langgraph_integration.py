from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.graph import GRAPH
from incident_agent.pipeline import run_deterministic_lifecycle


def test_graph_invoke_matches_pipeline_output() -> None:
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

    pipeline_state = run_deterministic_lifecycle(
        incident_id="inc-graph-1",
        alert=alert,
        logs=logs,
        events=events,
        top_n=3,
        target_ref="deployment/demo-app",
    )

    initial = IncidentState(
        incident_id="inc-graph-1",
        created_at=datetime.now(tz=UTC),
        phase="ingest",
        alert=alert,
        observations=Observations(logs=list(logs), events=list(events)),
    )
    initial.observations.extra["top_n"] = 3
    initial.observations.extra["target_ref"] = "deployment/demo-app"

    graph_state = GRAPH.invoke(initial)

    # Compare the serialized contract to avoid object identity differences.
    # `run_deterministic_lifecycle()` assigns created_at=now() internally, while the graph
    # preserves the provided `state.created_at`. Align timestamps before comparison.
    pipeline_state.created_at = graph_state.created_at
    assert graph_state.model_dump() == pipeline_state.model_dump()

