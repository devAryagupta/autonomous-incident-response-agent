"""Exhausted low-confidence path must hold: NOOP, no selected action, skip execute."""

from datetime import UTC, datetime

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.graph import GRAPH
from incident_agent.pipeline import run_deterministic_lifecycle
from incident_agent.routing import INSUFFICIENT_CONFIDENCE, NOOP_DECISION

# Vague CrashLoop signals — competing causes stay ambiguous after verify.
_AMBIGUOUS_LOGS = [
    "Back-off restarting failed container CrashLoopBackOff",
    "Exit Code: 1",
    "generic startup error",
]


def test_exhausted_ambiguous_case_skips_execution() -> None:
    created_at = datetime.now(tz=UTC)
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=created_at,
    )
    state = IncidentState(
        incident_id="inc-ambiguous-hold",
        created_at=created_at,
        phase="ingest",
        alert=alert,
        observations=Observations(logs=list(_AMBIGUOUS_LOGS), events=[]),
        max_replans=1,
        replan_count=0,
    )
    state.observations.extra["top_n"] = 3
    state.observations.extra["target_ref"] = "deployment/demo-app"
    state.observations.extra["confidence_threshold"] = 0.7

    out = GRAPH.invoke(state)

    assert out.confidence_score is not None
    assert out.confidence_score < 0.7
    assert out.replan_count == out.max_replans == 1
    assert len(out.hypotheses) >= 2

    assert out.decision == NOOP_DECISION
    assert out.decision_reason == INSUFFICIENT_CONFIDENCE
    assert out.chosen_remediation_id is None
    assert out.execution is not None
    assert out.execution.status == "skipped"
    assert out.execution.executed is False
    assert out.execution.details["reason"] == INSUFFICIENT_CONFIDENCE
    assert out.incident_resolved is False
    assert out.approval is None
    assert out.phase == "done"
    assert out.route == "escalate"


def test_exhausted_hold_matches_pipeline() -> None:
    created_at = datetime.now(tz=UTC)
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=created_at,
    )
    pipeline = run_deterministic_lifecycle(
        incident_id="inc-ambiguous-hold-parity",
        alert=alert,
        logs=list(_AMBIGUOUS_LOGS),
        events=[],
        top_n=3,
        target_ref="deployment/demo-app",
        max_replans=1,
        confidence_threshold=0.7,
        created_at=created_at,
    )
    initial = IncidentState(
        incident_id="inc-ambiguous-hold-parity",
        created_at=created_at,
        phase="ingest",
        alert=alert,
        observations=Observations(logs=list(_AMBIGUOUS_LOGS), events=[]),
        max_replans=1,
        replan_count=0,
    )
    initial.observations.extra["top_n"] = 3
    initial.observations.extra["target_ref"] = "deployment/demo-app"
    initial.observations.extra["confidence_threshold"] = 0.7
    graph = GRAPH.invoke(initial)

    assert pipeline.chosen_remediation_id is None
    assert graph.chosen_remediation_id is None
    assert pipeline.execution is not None and pipeline.execution.status == "skipped"
    assert graph.execution is not None and graph.execution.status == "skipped"
    assert pipeline.decision == graph.decision == NOOP_DECISION
    assert pipeline.model_dump() == graph.model_dump()
