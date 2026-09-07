"""Suggestion-only LLM boundary: providers + ingestion, no control-plane authority."""

from incident_agent.llm.ingestion import ingest_suggestion_payload, ingest_suggestions
from incident_agent.llm.openai_provider import OpenAILLMSuggestionProvider
from incident_agent.llm.provider import LLMSuggestionProvider, NoopLLMSuggestionProvider

__all__ = [
    "OpenAILLMSuggestionProvider",
    "LLMSuggestionProvider",
    "NoopLLMSuggestionProvider",
    "ingest_suggestion_payload",
    "ingest_suggestions",
]
