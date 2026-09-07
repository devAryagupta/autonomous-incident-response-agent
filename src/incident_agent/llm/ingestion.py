"""Deterministic ingestion gate for LLM suggestions.

This boundary validates model output and only emits advisory candidates that can
be consumed by existing deterministic logic. It never mutates control-plane
fields such as decision, route, approval, or execution.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from incident_agent.contracts import (
    BLOCKED_CONTROL_KEYS,
    AdvisoryEvidenceRequestCandidate,
    AdvisoryHypothesisCandidate,
    IncidentState,
    LLMSuggestion,
    LLMSuggestionResponse,
    Observations,
    SuggestionIngestionResult,
    SuggestionRejection,
    SuggestionStage,
)

_KNOWN_STAGES = frozenset({"hypothesize", "collect_evidence", "plan_fix", "verify_outcome"})
_KIND_BY_STAGE: dict[str, frozenset[str]] = {
    "hypothesize": frozenset({"hypothesis"}),
    "collect_evidence": frozenset({"evidence"}),
    "plan_fix": frozenset({"remediation"}),
    "verify_outcome": frozenset({"analysis"}),
}
_KNOWN_EVIDENCE_TYPES = frozenset({"metric", "log", "event", "describe"})
_KNOWN_QUERY_TYPES: dict[str, str] = {
    "container_memory_usage_bytes": "metric",
    "restart_history": "event",
    "container_memory_limits": "describe",
    "container_memory_working_set_bytes": "metric",
    "http_requests_per_second": "metric",
    "hpa_events": "event",
    "image_pull_events": "event",
    "pod_container_statuses": "describe",
    "image_pull_secrets": "describe",
    "secret_exists": "describe",
    "failed_mount_events": "event",
    "secret_references": "describe",
    "volume_mounts": "describe",
    "previous_container_logs": "log",
    "deployment_rollout_history": "describe",
    "configmap_refs": "describe",
    "pod_env": "describe",
    "dependency_endpoints": "describe",
    "pod_events_summary": "describe",
    "verification_probe": "describe",
}
_QUERY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_./:-]{1,127}$")
_CONTROL_INTENT_PATTERN = re.compile(r"\b(execute|approve|route|decision|bypass)\b")
_ACTION_PATTERN = re.compile(
    r"\b(restart_pod|rollback_deployment|rollout_restart|scale_deployment|"
    r"update_resource_limit|patch_resource)\b"
)
_REQUEST_PATTERN = re.compile(r"\b(?:request|collect|get|fetch)\s+([A-Za-z][A-Za-z0-9_./:-]+)\b")
UNSAFE_REJECTION_REASONS = frozenset({"forbidden_control_field", "control_plane_intent"})


def build_ingestion_audit(
    *,
    response: LLMSuggestionResponse,
    ingestion: SuggestionIngestionResult,
    accepted: list[dict[str, Any]],
    merged_count: int | None = None,
    useful_evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Serialize one stage's generated suggestions and gate decisions for traces."""
    unsafe = [
        item for item in ingestion.rejected if item.reason in UNSAFE_REJECTION_REASONS
    ]
    return {
        "provider": ingestion.provider,
        "model": ingestion.model,
        "generated": [item.model_dump(mode="json") for item in response.suggestions],
        "accepted": accepted,
        "rejections": [item.model_dump(mode="json") for item in ingestion.rejected],
        "generated_count": len(response.suggestions),
        "accepted_count": ingestion.accepted_count,
        "merged_count": ingestion.accepted_count if merged_count is None else merged_count,
        "rejected_count": ingestion.rejected_count,
        "unsafe_count": len(unsafe),
        "useful_evidence_ids": list(useful_evidence_ids or []),
        "warnings": list(response.warnings),
        "latency_ms": int(response.latency_ms),
    }


def record_ingestion_audit(
    observations: Observations,
    *,
    stage: str,
    payload: dict[str, Any],
) -> Observations:
    """Store latest-per-stage audit plus an append-only history for replans."""
    extra = dict(observations.extra)
    audit = dict(extra.get("llm_ingestion") or {})
    history = list(audit.get("history") or [])
    history.append({"stage": stage, **payload})
    audit[stage] = payload
    audit["history"] = history
    extra["llm_ingestion"] = audit
    return observations.model_copy(update={"extra": extra})


def ingest_suggestion_payload(
    *,
    state: IncidentState,
    payload: LLMSuggestionResponse | dict[str, Any],
) -> SuggestionIngestionResult:
    """Validate raw payload then ingest suggestions into advisory candidates."""
    if isinstance(payload, LLMSuggestionResponse):
        return ingest_suggestions(state=state, response=payload)

    try:
        response = LLMSuggestionResponse.model_validate(payload)
    except ValidationError as exc:
        return SuggestionIngestionResult(
            incident_id=state.incident_id,
            stage=_fallback_stage(state),
            provider="unknown",
            model="unknown",
            rejected=[
                SuggestionRejection(
                    suggestion_id=None,
                    reason="invalid_payload",
                    detail=str(exc),
                )
            ],
            warnings=["ingestion_failed"],
            accepted_count=0,
            rejected_count=1,
        )
    return ingest_suggestions(state=state, response=response)


def ingest_suggestions(
    *,
    state: IncidentState,
    response: LLMSuggestionResponse,
) -> SuggestionIngestionResult:
    """Gate LLM suggestions and emit advisory candidates only."""
    accepted_hypotheses: list[AdvisoryHypothesisCandidate] = []
    accepted_evidence: list[AdvisoryEvidenceRequestCandidate] = []
    rejected: list[SuggestionRejection] = []
    seen_hypotheses: set[str] = set()
    seen_evidence: set[str] = set()

    if response.stage not in _KNOWN_STAGES:
        rejected.append(
            SuggestionRejection(
                suggestion_id=None,
                reason="unknown_stage",
                detail=f"Unknown stage: {response.stage}",
            )
        )
        return SuggestionIngestionResult(
            incident_id=state.incident_id,
            stage=response.stage,
            provider=response.provider,
            model=response.model,
            accepted_hypotheses=[],
            accepted_evidence_requests=[],
            rejected=rejected,
            warnings=list(response.warnings),
            accepted_count=0,
            rejected_count=len(rejected),
        )

    for suggestion in response.suggestions:
        rejection = _validate_stage_kind(suggestion=suggestion, stage=response.stage)
        if rejection is not None:
            rejected.append(rejection)
            continue

        rejection = _reject_if_control_intent(suggestion)
        if rejection is not None:
            rejected.append(rejection)
            continue

        if suggestion.kind == "hypothesis":
            candidate, rejection = _ingest_hypothesis_candidate(
                suggestion=suggestion,
                seen=seen_hypotheses,
            )
            if rejection is not None:
                rejected.append(rejection)
                continue
            accepted_hypotheses.append(candidate)
            continue

        if suggestion.kind == "evidence":
            candidate, rejection = _ingest_evidence_candidate(
                state=state,
                suggestion=suggestion,
                seen=seen_evidence,
            )
            if rejection is not None:
                rejected.append(rejection)
                continue
            accepted_evidence.append(candidate)
            continue

        rejected.append(
            SuggestionRejection(
                suggestion_id=suggestion.suggestion_id,
                reason="unsupported_kind",
                detail=f"Kind {suggestion.kind} is not ingested in this milestone.",
            )
        )

    accepted_count = len(accepted_hypotheses) + len(accepted_evidence)
    return SuggestionIngestionResult(
        incident_id=state.incident_id,
        stage=response.stage,
        provider=response.provider,
        model=response.model,
        accepted_hypotheses=accepted_hypotheses,
        accepted_evidence_requests=accepted_evidence,
        rejected=rejected,
        warnings=list(response.warnings),
        accepted_count=accepted_count,
        rejected_count=len(rejected),
    )


def _ingest_hypothesis_candidate(
    *,
    suggestion: LLMSuggestion,
    seen: set[str],
) -> tuple[AdvisoryHypothesisCandidate, None] | tuple[None, SuggestionRejection]:
    description = suggestion.summary.strip()
    if len(description) < 3:
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="invalid_hypothesis",
            detail="Hypothesis summary must be at least 3 characters.",
        )

    key = description.lower()
    if key in seen:
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="duplicate_hypothesis",
            detail="Duplicate advisory hypothesis candidate.",
        )
    seen.add(key)

    return (
        AdvisoryHypothesisCandidate(
            suggestion_id=suggestion.suggestion_id,
            description=description,
            rationale=suggestion.rationale.strip(),
            metadata=dict(suggestion.metadata),
        ),
        None,
    )


def _ingest_evidence_candidate(
    *,
    state: IncidentState,
    suggestion: LLMSuggestion,
    seen: set[str],
) -> tuple[AdvisoryEvidenceRequestCandidate, None] | tuple[None, SuggestionRejection]:
    metadata = suggestion.metadata
    query = _as_str(metadata.get("query")) or _query_from_summary(suggestion.summary)
    if query is None or not _QUERY_PATTERN.fullmatch(query):
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="invalid_evidence_query",
            detail="Evidence query is missing or syntactically invalid.",
        )

    evidence_type = _as_str(metadata.get("type")) or _KNOWN_QUERY_TYPES.get(query)
    if evidence_type not in _KNOWN_EVIDENCE_TYPES:
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="invalid_evidence_type",
            detail="Evidence type is missing or not one of metric/log/event/describe.",
        )

    target = (
        _as_str(metadata.get("target"))
        or suggestion.target_ref
        or state_target_ref(state)
    )
    allowed_targets = _allowed_targets(state)
    if not _target_allowed(target, allowed_targets):
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="invalid_target",
            detail=f"Target {target!r} is outside allowed incident targets.",
        )

    hypothesis_id = _as_str(metadata.get("hypothesis_id")) or suggestion.hypothesis_id
    known_hypothesis_ids = {item.hypothesis_id for item in state.hypotheses}
    if hypothesis_id is not None and hypothesis_id not in known_hypothesis_ids:
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="unknown_hypothesis_id",
            detail=f"Unknown hypothesis_id: {hypothesis_id}",
        )

    key = f"{evidence_type}|{query}|{target}|{hypothesis_id or ''}".lower()
    if key in seen:
        return None, SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="duplicate_evidence_request",
            detail="Duplicate advisory evidence request candidate.",
        )
    seen.add(key)

    return (
        AdvisoryEvidenceRequestCandidate(
            suggestion_id=suggestion.suggestion_id,
            type=evidence_type,  # type: ignore[arg-type]
            query=query,
            target=target,
            rationale=suggestion.rationale.strip() or suggestion.summary.strip(),
            hypothesis_id=hypothesis_id,
            metadata=dict(suggestion.metadata),
        ),
        None,
    )


def _validate_stage_kind(
    *,
    suggestion: LLMSuggestion,
    stage: SuggestionStage,
) -> SuggestionRejection | None:
    allowed = _KIND_BY_STAGE.get(stage)
    if not allowed:
        return SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="unknown_stage",
            detail=f"Unknown stage {stage!r}",
        )
    if suggestion.kind not in allowed:
        return SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="stage_kind_mismatch",
            detail=f"Kind {suggestion.kind!r} is not allowed in stage {stage!r}.",
        )
    return None


def _reject_if_control_intent(suggestion: LLMSuggestion) -> SuggestionRejection | None:
    normalized = _normalize_text(f"{suggestion.summary} {suggestion.rationale}")
    blocked_words = sorted(
        key for key in BLOCKED_CONTROL_KEYS if re.search(rf"\b{re.escape(key)}\b", normalized)
    )
    if blocked_words:
        return SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="forbidden_control_field",
            detail=f"Suggestion references blocked control fields: {', '.join(blocked_words)}",
        )
    if _CONTROL_INTENT_PATTERN.search(normalized) and _ACTION_PATTERN.search(normalized):
        return SuggestionRejection(
            suggestion_id=suggestion.suggestion_id,
            reason="control_plane_intent",
            detail="Suggestion attempts control-plane action intent.",
        )
    return None


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().replace("-", "_").split())


def _query_from_summary(summary: str) -> str | None:
    match = _REQUEST_PATTERN.search(summary)
    if not match:
        return None
    return match.group(1)


def _target_allowed(target: str, allowed_targets: set[str]) -> bool:
    candidate = target.strip().lower()
    return candidate in allowed_targets


def _allowed_targets(state: IncidentState) -> set[str]:
    targets = {state_target_ref(state).strip().lower()}
    resource = state.resource
    if resource is not None and resource.name:
        targets.add(resource.name.strip().lower())
        if resource.kind:
            targets.add(f"{resource.kind}/{resource.name}".strip().lower())
    labels = state.alert.labels
    for key in ("service", "app", "workload", "deployment", "pod"):
        value = labels.get(key)
        if value:
            targets.add(value.strip().lower())
    return {item for item in targets if item}


def state_target_ref(state: IncidentState) -> str:
    target_ref = state.observations.extra.get("target_ref")
    if isinstance(target_ref, str) and target_ref:
        return target_ref
    labels = state.alert.labels
    for key in ("service", "app", "workload", "deployment"):
        value = labels.get(key)
        if value:
            return str(value)
    if state.resource is not None and state.resource.name:
        kind = state.resource.kind or "workload"
        return f"{kind}/{state.resource.name}"
    return state.alert.alert_name or "<workload>"


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _fallback_stage(state: IncidentState) -> SuggestionStage:
    if state.phase in _KNOWN_STAGES:
        return state.phase  # type: ignore[return-value]
    return "hypothesize"
