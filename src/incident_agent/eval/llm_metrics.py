"""Aggregate advisory-LLM metrics from golden reasoning traces.

These numbers measure model activity and contract compliance. They do not
replace diagnosis/fix accuracy — the LLM is not the diagnoser.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from incident_agent.eval.metrics import normalize_cause
from incident_agent.llm.ingestion import UNSAFE_REJECTION_REASONS

_UNSAFE_REASONS = UNSAFE_REJECTION_REASONS


@dataclass(frozen=True, slots=True)
class LLMSuggestionRecord:
    suggestion_id: str | None
    stage: str
    kind: str | None
    summary: str
    rationale: str
    accepted: bool
    rejection_reason: str | None
    rejection_detail: str | None
    useful_evidence: bool = False
    unsafe: bool = False


@dataclass(frozen=True, slots=True)
class LLMStageSnapshot:
    stage: str
    provider: str
    model: str
    generated_count: int
    accepted_count: int
    rejected_count: int
    useful_evidence_count: int
    unsafe_count: int
    warnings: list[str] = field(default_factory=list)
    suggestions: list[LLMSuggestionRecord] = field(default_factory=list)
    latency_ms: int = 0


@dataclass(frozen=True, slots=True)
class LLMRunTotals:
    suggestions_generated: int
    suggestions_accepted: int
    suggestions_rejected: int
    useful_evidence_requests: int
    unsafe_control_suggestions: int
    unsafe_accepted: int
    acceptance_rate: float | None
    provider: str | None = None
    model: str | None = None


def hypotheses_match(predicted: str | None, expected: str | None) -> bool:
    """Exact normalized hypothesis-label match (not diagnosis-alias buckets)."""
    if not predicted or not expected:
        return False
    return normalize_cause(predicted) == normalize_cause(expected)


def snapshot_from_audit(audit: dict[str, Any] | None, *, stage: str) -> LLMStageSnapshot:
    """Turn one node's llm_ingestion payload into a trace snapshot."""
    payload = dict(audit or {})
    generated = [item for item in payload.get("generated") or [] if isinstance(item, dict)]
    accepted = [item for item in payload.get("accepted") or [] if isinstance(item, dict)]
    rejections = [item for item in payload.get("rejections") or [] if isinstance(item, dict)]
    useful_ids = {str(item) for item in payload.get("useful_evidence_ids") or []}
    records = _records_from_audit(
        stage=stage,
        generated=generated,
        accepted=accepted,
        rejections=rejections,
        useful_ids=useful_ids,
    )
    return LLMStageSnapshot(
        stage=stage,
        provider=str(payload.get("provider") or "none"),
        model=str(payload.get("model") or "none"),
        generated_count=int(payload.get("generated_count") or len(generated)),
        accepted_count=int(payload.get("accepted_count") or len(accepted)),
        rejected_count=int(payload.get("rejected_count") or len(rejections)),
        useful_evidence_count=len(useful_ids),
        unsafe_count=int(payload.get("unsafe_count") or _count_unsafe(records)),
        warnings=[str(item) for item in payload.get("warnings") or []],
        suggestions=records,
        latency_ms=int(payload.get("latency_ms") or 0),
    )


def aggregate_llm_snapshots(snapshots: list[LLMStageSnapshot]) -> LLMRunTotals:
    generated = sum(item.generated_count for item in snapshots)
    accepted = sum(item.accepted_count for item in snapshots)
    rejected = sum(item.rejected_count for item in snapshots)
    useful = sum(item.useful_evidence_count for item in snapshots)
    unsafe = sum(1 for snap in snapshots for rec in snap.suggestions if rec.unsafe)
    unsafe_accepted = sum(
        1 for snap in snapshots for rec in snap.suggestions if rec.unsafe and rec.accepted
    )
    provider = next((item.provider for item in snapshots if item.provider != "none"), None)
    model = next((item.model for item in snapshots if item.model != "none"), None)
    rate = (accepted / generated) if generated else None
    return LLMRunTotals(
        suggestions_generated=generated,
        suggestions_accepted=accepted,
        suggestions_rejected=rejected,
        useful_evidence_requests=useful,
        unsafe_control_suggestions=unsafe,
        unsafe_accepted=unsafe_accepted,
        acceptance_rate=rate,
        provider=provider,
        model=model,
    )


def totals_to_dict(totals: LLMRunTotals) -> dict[str, Any]:
    payload = asdict(totals)
    if totals.acceptance_rate is not None:
        payload["acceptance_rate"] = round(totals.acceptance_rate, 4)
    return payload


def _records_from_audit(
    *,
    stage: str,
    generated: list[dict[str, Any]],
    accepted: list[dict[str, Any]],
    rejections: list[dict[str, Any]],
    useful_ids: set[str],
) -> list[LLMSuggestionRecord]:
    rejected_by_id = {
        _suggestion_id(item): item for item in rejections if _suggestion_id(item)
    }
    accepted_by_id = {
        _suggestion_id(item): item for item in accepted if _suggestion_id(item)
    }
    seen: set[str] = set()
    records: list[LLMSuggestionRecord] = []

    for item in generated:
        sid = _suggestion_id(item)
        seen.add(sid or "")
        rejection = rejected_by_id.get(sid or "")
        accepted_item = accepted_by_id.get(sid or "")
        reason = str(rejection.get("reason")) if rejection else None
        records.append(
            LLMSuggestionRecord(
                suggestion_id=sid,
                stage=stage,
                kind=_as_str(item.get("kind")),
                summary=_summary_of(item, accepted_item),
                rationale=_rationale_of(item, accepted_item),
                accepted=accepted_item is not None and rejection is None,
                rejection_reason=reason,
                rejection_detail=(
                    str(rejection.get("detail")) if rejection and rejection.get("detail") else None
                ),
                useful_evidence=sid in useful_ids if sid else False,
                unsafe=_is_unsafe(reason),
            )
        )

    for item in rejections:
        sid = _suggestion_id(item)
        if sid and sid in seen:
            continue
        reason = str(item.get("reason") or "")
        records.append(
            LLMSuggestionRecord(
                suggestion_id=sid,
                stage=stage,
                kind=None,
                summary="",
                rationale="",
                accepted=False,
                rejection_reason=reason or None,
                rejection_detail=_as_str(item.get("detail")),
                useful_evidence=False,
                unsafe=_is_unsafe(reason),
            )
        )
    return records


def _suggestion_id(item: dict[str, Any]) -> str | None:
    value = item.get("suggestion_id")
    return value if isinstance(value, str) and value.strip() else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _summary_of(generated: dict[str, Any], accepted: dict[str, Any] | None) -> str:
    if accepted:
        description = accepted.get("description") or accepted.get("query")
        return str(description or generated.get("summary") or "")
    return str(generated.get("summary") or "")


def _rationale_of(generated: dict[str, Any], accepted: dict[str, Any] | None) -> str:
    if accepted and accepted.get("rationale"):
        return str(accepted["rationale"])
    return str(generated.get("rationale") or "")


def _is_unsafe(reason: str | None) -> bool:
    return bool(reason) and reason in _UNSAFE_REASONS


def _count_unsafe(records: list[LLMSuggestionRecord]) -> int:
    return sum(1 for item in records if item.unsafe)
