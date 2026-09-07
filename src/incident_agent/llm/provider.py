"""LLM suggestion provider boundary.

This module defines the interface that future model clients must satisfy.
Providers return advisory suggestions only; pipeline control remains deterministic.
"""

from __future__ import annotations

from typing import Protocol

from incident_agent.contracts import (
    LLMSuggestionRequest,
    LLMSuggestionResponse,
)


class LLMSuggestionProvider(Protocol):
    """Contract for model-backed suggestion providers."""

    def suggest(self, request: LLMSuggestionRequest) -> LLMSuggestionResponse: ...


class NoopLLMSuggestionProvider:
    """Deterministic default provider: emits no suggestions."""

    def suggest(self, request: LLMSuggestionRequest) -> LLMSuggestionResponse:
        return LLMSuggestionResponse(
            provider="none",
            model="none",
            stage=request.stage,
            suggestions=[],
            warnings=["llm_disabled"],
            latency_ms=0,
        )
