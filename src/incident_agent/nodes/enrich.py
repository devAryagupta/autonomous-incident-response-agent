"""Enrich node: populate IncidentState via abstract providers (DI)."""

from __future__ import annotations

from typing import Any

from incident_agent.contracts import IncidentState, Observations
from incident_agent.providers import ProviderBundle, default_providers, resolve_providers


def enrich(
    state: IncidentState,
    *,
    providers: ProviderBundle | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """
    Provider-backed ingest/enrich.

    Nodes downstream read only IncidentState. This is the sole place that
    talks to ObservationProvider / MetricsProvider / MemoryProvider so that
    Kubernetes/Prometheus/Chroma can swap in later without changing diagnose/
    hypothesize/plan_fix/…
    """
    bundle = providers or resolve_providers(config)

    observations = bundle.observations.fetch_observations(state)
    metrics = bundle.metrics.fetch_metrics(state)
    similar = bundle.memory.query_similar(state, k=3)

    extra = dict(observations.extra)
    extra["metrics"] = metrics
    extra.setdefault("observation_provider", type(bundle.observations).__name__)
    extra.setdefault("metrics_provider", type(bundle.metrics).__name__)
    extra.setdefault("memory_provider", type(bundle.memory).__name__)

    enriched = Observations(
        schema_version=observations.schema_version,
        logs=list(observations.logs),
        events=list(observations.events),
        extra=extra,
    )

    log = list(state.log)
    log.append(
        "enrich: "
        f"logs={len(enriched.logs)} events={len(enriched.events)} "
        f"similar={len(similar)} metrics_keys={list(metrics.keys())}"
    )

    return {
        "observations": enriched,
        "similar_incidents": similar,
        "phase": "ingest",
        "log": log,
    }


def enrich_with_defaults(state: IncidentState) -> dict[str, object]:
    """Convenience for call sites that do not inject providers."""
    return enrich(state, providers=default_providers())
