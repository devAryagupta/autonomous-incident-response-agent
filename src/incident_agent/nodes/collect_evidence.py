"""Collect-evidence node: plan curiosity requests and fulfill via providers."""

from __future__ import annotations

from typing import Any

from incident_agent.contracts import (
    EvidenceRequest,
    EvidenceResult,
    IncidentState,
    LLMSuggestionRequest,
    Observations,
)
from incident_agent.evidence import plan_evidence_requests
from incident_agent.llm import ingest_suggestions
from incident_agent.llm.ingestion import build_ingestion_audit, record_ingestion_audit
from incident_agent.providers import ProviderBundle, resolve_providers
from incident_agent.providers.fulfill import fulfill_evidence_requests

_MAX_EVIDENCE_CONTEXT = 8


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
        if result.data.get("provider") == "prometheus":
            metrics["provider"] = "prometheus"
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


def _request_for_evidence(state: IncidentState) -> LLMSuggestionRequest:
    evidence = list(state.observations.logs[-_MAX_EVIDENCE_CONTEXT:])
    evidence.extend(state.observations.events[-_MAX_EVIDENCE_CONTEXT:])
    return LLMSuggestionRequest(
        incident_id=state.incident_id,
        stage="collect_evidence",
        diagnosis_category=state.diagnosis.category if state.diagnosis else None,
        diagnosis_summary=state.diagnosis.summary if state.diagnosis else None,
        hypotheses=[item.description for item in state.hypotheses],
        evidence=evidence,
        target_ref=str(state.observations.extra.get("target_ref") or "") or None,
    )


def _advisory_requests(
    ingestion_payload: list[dict[str, object]],
) -> list[EvidenceRequest]:
    requests: list[EvidenceRequest] = []
    for idx, item in enumerate(ingestion_payload, start=1):
        requests.append(
            EvidenceRequest(
                request_id=f"er-llm-{idx}",
                type=str(item["type"]),  # type: ignore[arg-type]
                query=str(item["query"]),
                target=str(item["target"]),
                hypothesis_id=(
                    str(item["hypothesis_id"])
                    if item.get("hypothesis_id") is not None
                    else None
                ),
                rationale=str(item.get("rationale") or ""),
            )
        )
    return requests


def _merge_requests(
    deterministic: list[EvidenceRequest],
    advisory: list[EvidenceRequest],
) -> list[EvidenceRequest]:
    seen: set[str] = set()
    merged: list[EvidenceRequest] = []
    for request in [*deterministic, *advisory]:
        key = f"{request.type}|{request.query}|{request.target}".lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(request)
    return merged


def _useful_evidence_ids(
    *,
    advisory: list[EvidenceRequest],
    merged: list[EvidenceRequest],
    results: list[EvidenceResult],
) -> list[str]:
    """Accepted LLM probes that were new and produced a usable result."""
    surviving = {item.request_id for item in merged}
    kept = [item for item in advisory if item.request_id in surviving]
    kept_ids = {item.request_id for item in kept}
    useful: list[str] = []
    for result in results:
        if result.request_id not in kept_ids:
            continue
        if result.success or bool((result.summary or "").strip()):
            useful.append(result.request_id)
    return useful


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
    deterministic_requests = plan_evidence_requests(state)
    response = bundle.llm.suggest(_request_for_evidence(state))
    ingestion = ingest_suggestions(state=state, response=response)
    advisory_payload = [
        item.model_dump(mode="python")
        for item in ingestion.accepted_evidence_requests
    ]
    advisory_requests = _advisory_requests(advisory_payload)
    requests = _merge_requests(deterministic_requests, advisory_requests)
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
    useful_ids = _useful_evidence_ids(
        advisory=advisory_requests,
        merged=requests,
        results=results,
    )
    audit_payload = build_ingestion_audit(
        response=response,
        ingestion=ingestion,
        accepted=advisory_payload,
        merged_count=len(advisory_requests),
        useful_evidence_ids=useful_ids,
    )
    observations = record_ingestion_audit(
        observations,
        stage="collect_evidence",
        payload=audit_payload,
    )

    log = list(state.log)
    log.append(
        "collect_evidence: "
        f"deterministic_requests={len(deterministic_requests)} "
        f"llm_accepted={len(advisory_requests)} llm_rejected={ingestion.rejected_count} "
        f"requests={len(requests)} results={len(results)} "
        f"summaries={sum(1 for r in results if r.summary)}"
    )
    for rejection in ingestion.rejected:
        sid = rejection.suggestion_id or "<none>"
        log.append(
            f"collect_evidence: llm_rejected suggestion_id={sid} "
            f"reason={rejection.reason}"
        )

    return {
        "evidence_requests": requests,
        "evidence_results": results,
        "observations": observations,
        "log": log,
    }
