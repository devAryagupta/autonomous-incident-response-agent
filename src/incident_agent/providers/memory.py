"""Memory provider implementations (Stage-0: NoMemory)."""

from __future__ import annotations

from typing import Any

from incident_agent.contracts import IncidentState


class NoMemoryProvider:
    """
    Disabled memory backend.

    Later:
      - ChromaMemoryProvider
      - SQLMemoryProvider
    """

    def query_similar(self, state: IncidentState, *, k: int = 3) -> list[dict[str, Any]]:
        _ = state
        _ = k
        return []

    def store(self, state: IncidentState) -> str | None:
        _ = state
        return None
