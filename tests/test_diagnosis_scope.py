from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    IncidentState,
    Observations,
)
from incident_agent.diagnosis import assess_diagnosis_scope
from incident_agent.nodes.hypothesize import hypothesize
from incident_agent.nodes.verify_hypotheses import verify_hypotheses
from incident_agent.routing import (
    DIAGNOSIS_SCOPE_INVALID,
    NOOP_DECISION,
    escalate_insufficient_confidence,
    route_on_confidence,
)


def _state(*, diagnosis: Diagnosis, logs: list[str], events: list[str] | None = None) -> IncidentState:
    return IncidentState(
        incident_id="inc-scope",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=logs, events=list(events or [])),
        diagnosis=diagnosis,
    )


def test_mixed_signals_keep_frozen_oom_scope() -> None:
    diagnosis = Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9)
    updated = assess_diagnosis_scope(
        diagnosis,
        Observations(
            logs=["OOMKilled: exit status 137", "secret db-credentials not found"],
            events=["Warning  FailedMount  kubelet  secret not found"],
        ),
    )
    assert updated.scope_valid is True
    assert updated.category == "OOMKilled"


def test_config_only_evidence_invalidates_frozen_oom_scope() -> None:
    diagnosis = Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9)
    updated = assess_diagnosis_scope(
        diagnosis,
        Observations(
            logs=["configuration missing: SECRET_KEY not set"],
            events=[
                'Warning  FailedMount kubelet  MountVolume.SetUp failed for volume '
                '"secret": secret "db-credentials" not found',
            ],
        ),
    )
    assert updated.scope_valid is False
    assert "Invalid Configuration" in updated.scope_invalid_reason
    assert updated.category == "OOMKilled"


def test_invalid_scope_beats_high_confidence() -> None:
    state = _state(
        diagnosis=Diagnosis(
            summary="OOMKilled",
            category="OOMKilled",
            confidence=0.9,
            scope_valid=False,
            scope_invalid_reason="Frozen scope OOMKilled is unsupported",
        ),
        logs=["configuration missing"],
    )
    state.confidence_score = 0.95
    state.replan_count = 0
    state.max_replans = 2
    assert route_on_confidence(state) == "escalate"


def test_scope_contradiction_escalates_instead_of_replanning() -> None:
    state = _state(
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9),
        logs=["configuration missing: SECRET_KEY not set"],
        events=[
            'Warning  FailedMount kubelet  MountVolume.SetUp failed for volume '
            '"secret": secret "db-credentials" not found',
        ],
    )
    for key, value in hypothesize(state).items():
        setattr(state, key, value)
    for key, value in verify_hypotheses(state).items():
        setattr(state, key, value)

    assert state.diagnosis is not None
    assert state.diagnosis.scope_valid is False
    assert state.diagnosis.category == "OOMKilled"
    assert route_on_confidence(state) == "escalate"

    hold = escalate_insufficient_confidence(state)
    assert hold["decision"] == NOOP_DECISION
    assert hold["decision_reason"] == DIAGNOSIS_SCOPE_INVALID
    assert hold["chosen_remediation_id"] is None
    assert hold["execution"].status == "skipped"  # type: ignore[union-attr]
    assert hold["execution"].details["reason"] == DIAGNOSIS_SCOPE_INVALID  # type: ignore[union-attr]
