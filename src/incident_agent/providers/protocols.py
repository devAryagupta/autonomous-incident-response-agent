"""Abstract provider interfaces.

Nodes and the graph must depend only on these Protocols.
Live backends (K8s, Prometheus, Chroma, …) plug in later without changing nodes.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from incident_agent.contracts import (
    EvidenceRequest,
    EvidenceResult,
    ExecutionResult,
    FixPlan,
    IncidentState,
    Observations,
)


@runtime_checkable
class ObservationProvider(Protocol):
    """Source of logs/events/describe-style signals for an incident."""

    def fetch_observations(self, state: IncidentState) -> Observations:
        """Return observations for the incident (may enrich or pass through state)."""
        ...

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        """Fulfill a log/event/describe EvidenceRequest."""
        ...


@runtime_checkable
class MetricsProvider(Protocol):
    """Source of metric snapshots for an incident."""

    def fetch_metrics(self, state: IncidentState) -> dict[str, Any]:
        """Return a metrics payload (PromQL-shaped or synthetic stub)."""
        ...

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        """Fulfill a metric EvidenceRequest (e.g. PromQL query)."""
        ...


@runtime_checkable
class ExecutionProvider(Protocol):
    """Applies a FixPlan (or dry-runs it). Never called with live I/O in Stage 0."""

    def execute(self, plan: FixPlan, *, state: IncidentState) -> ExecutionResult:
        """Execute (or simulate) the plan and return an ExecutionResult."""
        ...


@runtime_checkable
class MemoryProvider(Protocol):
    """Historical incident memory for retrieval / learning."""

    def query_similar(self, state: IncidentState, *, k: int = 3) -> list[dict[str, Any]]:
        """Return similar past incidents (empty for NoMemory)."""
        ...

    def store(self, state: IncidentState) -> str | None:
        """Persist an incident; return a memory id (or None if disabled)."""
        ...
