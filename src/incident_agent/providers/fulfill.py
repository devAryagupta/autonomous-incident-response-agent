"""Dispatch EvidenceRequests to the appropriate provider."""

from __future__ import annotations

from incident_agent.contracts import EvidenceRequest, EvidenceResult, IncidentState
from incident_agent.providers.bundle import ProviderBundle


def fulfill_evidence_requests(
    *,
    bundle: ProviderBundle,
    requests: list[EvidenceRequest],
    state: IncidentState,
) -> list[EvidenceResult]:
    """Execute planned evidence requests via Observation/Metrics providers."""
    results: list[EvidenceResult] = []
    for request in requests:
        if request.type == "metric":
            results.append(bundle.metrics.execute_evidence_request(request, state=state))
        else:
            results.append(bundle.observations.execute_evidence_request(request, state=state))
    return results
