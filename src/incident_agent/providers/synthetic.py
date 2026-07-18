"""Synthetic / Stage-0 provider implementations (no live infrastructure)."""

from __future__ import annotations

from typing import Any

from incident_agent.contracts import IncidentState, Observations


class SyntheticObservationProvider:
    """
    Pass-through observations from IncidentState.

    Stage-0: dataset / fixture already populated `state.observations`.
    Later: KubernetesObservationProvider will fetch live logs/events.
    """

    def fetch_observations(self, state: IncidentState) -> Observations:
        return Observations(
            schema_version=state.observations.schema_version,
            logs=list(state.observations.logs),
            events=list(state.observations.events),
            extra=dict(state.observations.extra),
        )


class SyntheticMetricsProvider:
    """
    Deterministic empty metrics stub.

    Later: PrometheusMetricsProvider will query PromQL.
    """

    def fetch_metrics(self, state: IncidentState) -> dict[str, Any]:
        _ = state
        return {
            "provider": "synthetic",
            "series": {},
            "notes": "No live metrics in Stage 0",
        }
