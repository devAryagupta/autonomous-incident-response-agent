from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Hypothesis,
    IncidentState,
    LLMSuggestion,
    LLMSuggestionResponse,
    Observations,
)
from incident_agent.llm import ingest_suggestion_payload, ingest_suggestions


def _state() -> IncidentState:
    state = IncidentState(
        incident_id="inc-llm-ingest",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
            labels={"deployment": "demo-app"},
        ),
        observations=Observations(
            logs=["Back-off restarting failed container"],
            events=[],
            extra={"target_ref": "deployment/demo-app"},
        ),
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1",
                description="Memory leak",
                likelihood=0.4,
            )
        ],
    )
    return state


def test_ingestion_rejects_execute_rollout_restart_immediately() -> None:
    response = LLMSuggestionResponse(
        provider="mock",
        model="mock-v1",
        stage="plan_fix",
        suggestions=[
            LLMSuggestion(
                suggestion_id="s-bad-exec",
                stage="plan_fix",
                kind="remediation",
                summary="execute rollout_restart immediately",
            )
        ],
    )

    result = ingest_suggestions(state=_state(), response=response)

    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert result.rejected[0].suggestion_id == "s-bad-exec"
    assert result.rejected[0].reason in {"forbidden_control_field", "control_plane_intent"}


def test_ingestion_rejects_unknown_stage_payload() -> None:
    result = ingest_suggestion_payload(
        state=_state(),
        payload={
            "provider": "mock",
            "model": "mock-v1",
            "stage": "pwn_stage",
            "suggestions": [],
        },
    )

    assert result.accepted_count == 0
    assert result.rejected_count == 1
    assert result.rejected[0].reason == "invalid_payload"


def test_ingestion_accepts_advisory_hypothesis_candidate() -> None:
    response = LLMSuggestionResponse(
        provider="mock",
        model="mock-v1",
        stage="hypothesize",
        suggestions=[
            LLMSuggestion(
                suggestion_id="s-hyp-1",
                stage="hypothesize",
                kind="hypothesis",
                summary="Memory leak is likely",
                rationale="Heap growth appears across restart cycles.",
            )
        ],
    )

    result = ingest_suggestions(state=_state(), response=response)

    assert result.rejected_count == 0
    assert result.accepted_count == 1
    assert len(result.accepted_hypotheses) == 1
    assert result.accepted_hypotheses[0].description == "Memory leak is likely"


def test_ingestion_accepts_advisory_evidence_request_candidate() -> None:
    response = LLMSuggestionResponse(
        provider="mock",
        model="mock-v1",
        stage="collect_evidence",
        suggestions=[
            LLMSuggestion(
                suggestion_id="s-ev-1",
                stage="collect_evidence",
                kind="evidence",
                summary="request previous_container_logs",
            )
        ],
    )

    result = ingest_suggestions(state=_state(), response=response)

    assert result.rejected_count == 0
    assert result.accepted_count == 1
    assert len(result.accepted_evidence_requests) == 1
    candidate = result.accepted_evidence_requests[0]
    assert candidate.query == "previous_container_logs"
    assert candidate.type == "log"
    assert candidate.target == "deployment/demo-app"
