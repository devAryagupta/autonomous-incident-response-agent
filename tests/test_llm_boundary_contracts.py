from __future__ import annotations

import pytest
from pydantic import ValidationError

from incident_agent.contracts import (
    LLMSuggestion,
    LLMSuggestionRequest,
    LLMSuggestionResponse,
)
from incident_agent.llm import NoopLLMSuggestionProvider


def test_suggestion_rejects_control_plane_metadata() -> None:
    with pytest.raises(ValidationError):
        LLMSuggestion(
            suggestion_id="s-1",
            stage="plan_fix",
            kind="remediation",
            summary="Use rollback",
            metadata={"decision": "execute"},
        )


def test_response_enforces_single_stage_scope() -> None:
    with pytest.raises(ValidationError):
        LLMSuggestionResponse(
            provider="mock",
            model="mock-v1",
            stage="collect_evidence",
            suggestions=[
                LLMSuggestion(
                    suggestion_id="s-1",
                    stage="plan_fix",
                    kind="analysis",
                    summary="Wrong stage",
                )
            ],
        )


def test_noop_provider_returns_empty_advisory_response() -> None:
    provider = NoopLLMSuggestionProvider()
    request = LLMSuggestionRequest(
        incident_id="inc-1",
        stage="hypothesize",
        diagnosis_category="OOMKilled",
        diagnosis_summary="Container OOMKilled during startup",
    )

    response = provider.suggest(request)

    assert response.stage == "hypothesize"
    assert response.suggestions == []
    assert response.warnings == ["llm_disabled"]
