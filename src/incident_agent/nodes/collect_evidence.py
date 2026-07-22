"""Collect-evidence node: plan curiosity requests and fulfill via providers."""

from __future__ import annotations

from typing import Any

from incident_agent.contracts import EvidenceResult, IncidentState, Observations
from incident_agent.evidence import plan_evidence_requests
from incident_agent.providers import ProviderBundle, resolve_providers
from incident_agent.providers.fulfill import fulfill_evidence_requests


def _span_mi(block: dict[str, Any]) -> float:
    try:
        return float(block.get("end", 0)) - float(block.get("start", 0))
    except (TypeError, ValueError):
        return 0.0


def _merge_metrics(extra: dict[str, Any], results: list[EvidenceResult]) -> dict[str, Any]:
    metrics = dict(extra.get("metrics") or {})
    series = dict(metrics.get("series") or {})
    for result in results:
        if not result.success:
            continue
        if "memory_mi" in result.data and isinstance(result.data["memory_mi"], dict):
            incoming = result.data["memory_mi"]
            existing = metrics.get("memory_mi")
            if not isinstance(existing, dict) or _span_mi(incoming) >= _span_mi(existing):
                metrics["memory_mi"] = incoming
        if result.type == "metric" and "series" in result.data:
            series[result.query] = result.data["series"]
        if "rps" in result.data:
            metrics["rps"] = result.data["rps"]
        if "working_set_mi" in result.data:
            metrics["working_set_mi"] = result.data["working_set_mi"]
            metrics["limit_mi"] = result.data.get("limit_mi")
    if series:
        metrics["series"] = series
    metrics.setdefault("provider", metrics.get("provider", "synthetic"))
    return metrics


def _append_summaries(
    *,
    logs: list[str],
    events: list[str],
    results: list[EvidenceResult],
) -> tuple[list[str], list[str]]:
    next_logs = list(logs)
    next_events = list(events)
    for result in results:
        summary = (result.summary or "").strip()
        if not summary:
            continue
        if result.type in {"event", "describe"}:
            if summary not in next_events:
                next_events.append(summary)
        else:
            if summary not in next_logs:
                next_logs.append(summary)
    return next_logs, next_events


def collect_evidence(
    state: IncidentState,
    *,
    providers: ProviderBundle | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """
    Hypothesis → EvidenceRequest → provider fulfillment → enriched observations.

    Business planning stays in `evidence.planner`; I/O stays in providers.
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before collect_evidence()")

    bundle = providers or resolve_providers(config)
    requests = plan_evidence_requests(state)
    results = fulfill_evidence_requests(bundle=bundle, requests=requests, state=state)

    extra = dict(state.observations.extra)
    extra["metrics"] = _merge_metrics(extra, results)
    extra["collected_evidence"] = [r.model_dump() for r in results]

    logs, events = _append_summaries(
        logs=state.observations.logs,
        events=state.observations.events,
        results=results,
    )
    observations = Observations(
        schema_version=state.observations.schema_version,
        logs=logs,
        events=events,
        extra=extra,
    )

    log = list(state.log)
    log.append(
        "collect_evidence: "
        f"requests={len(requests)} results={len(results)} "
        f"summaries={sum(1 for r in results if r.summary)}"
    )

    return {
        "evidence_requests": requests,
        "evidence_results": results,
        "observations": observations,
        "log": log,
    }
