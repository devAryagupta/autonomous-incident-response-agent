from __future__ import annotations

from typing import Any

from incident_agent.contracts import (
    Evidence,
    Hypothesis,
    IncidentState,
    LLMSuggestionRequest,
)
from incident_agent.hypothesis import hypothesize_from_state
from incident_agent.llm import ingest_suggestions
from incident_agent.llm.ingestion import build_ingestion_audit, record_ingestion_audit
from incident_agent.providers import ProviderBundle, resolve_providers

_LLM_ADVISORY_PRIOR = 0.05
_MAX_EVIDENCE_CONTEXT = 8


def _request_for_hypotheses(
    state: IncidentState,
    deterministic: list[Hypothesis],
) -> LLMSuggestionRequest:
    evidence = list(state.observations.logs[-_MAX_EVIDENCE_CONTEXT:])
    evidence.extend(state.observations.events[-_MAX_EVIDENCE_CONTEXT:])
    return LLMSuggestionRequest(
        incident_id=state.incident_id,
        stage="hypothesize",
        diagnosis_category=state.diagnosis.category if state.diagnosis else None,
        diagnosis_summary=state.diagnosis.summary if state.diagnosis else None,
        hypotheses=[item.description for item in deterministic],
        evidence=evidence,
        target_ref=str(state.observations.extra.get("target_ref") or "") or None,
    )


def _normalized_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    total = sum(float(item.likelihood) for item in hypotheses)
    if total <= 0:
        return hypotheses
    return [
        item.model_copy(update={"likelihood": round(float(item.likelihood) / total, 4)})
        for item in hypotheses
    ]


def _merge_advisory_hypotheses(
    *,
    deterministic: list[Hypothesis],
    accepted: list[dict[str, object]],
) -> tuple[list[Hypothesis], int]:
    existing = {item.description.strip().lower() for item in deterministic}
    advisory: list[Hypothesis] = []
    accepted_count = 0
    for idx, item in enumerate(accepted, start=1):
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        if description.lower() in existing:
            continue
        existing.add(description.lower())
        rationale = str(item.get("rationale", "")).strip()
        evidence = [
            Evidence(
                source="other",
                text=f"LLM advisory: {rationale or description}",
            )
        ]
        advisory.append(
            Hypothesis(
                hypothesis_id=f"h-llm-{idx}",
                description=description,
                likelihood=_LLM_ADVISORY_PRIOR,
                evidence=evidence,
                verification_checks=[],
                remediation_key=None,
            )
        )
        accepted_count += 1
    return _normalized_hypotheses([*deterministic, *advisory]), accepted_count


def hypothesize(
    state: IncidentState,
    *,
    providers: ProviderBundle | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """
    Hypothesize node (state-in, partial-state-out).

    Delegates to the deterministic hypothesis engine. Diagnosis names the
    symptom; hypotheses are competing causes with normalized *prior belief*
    in ``Hypothesis.likelihood``. Verification later writes posterior belief.
    """
    deterministic = hypothesize_from_state(state)
    bundle = providers or resolve_providers(config)
    response = bundle.llm.suggest(_request_for_hypotheses(state, deterministic))
    ingestion = ingest_suggestions(state=state, response=response)
    accepted_payload = [item.model_dump(mode="python") for item in ingestion.accepted_hypotheses]
    hypotheses, accepted_count = _merge_advisory_hypotheses(
        deterministic=deterministic,
        accepted=accepted_payload,
    )

    log = list(state.log)
    log.append(
        "hypothesize: "
        f"deterministic={len(deterministic)} llm_accepted={accepted_count} "
        f"llm_rejected={ingestion.rejected_count}"
    )
    for rejection in ingestion.rejected:
        sid = rejection.suggestion_id or "<none>"
        log.append(
            f"hypothesize: llm_rejected suggestion_id={sid} "
            f"reason={rejection.reason}"
        )

    audit_payload = build_ingestion_audit(
        response=response,
        ingestion=ingestion,
        accepted=accepted_payload,
        merged_count=accepted_count,
    )
    observations = record_ingestion_audit(
        state.observations,
        stage="hypothesize",
        payload=audit_payload,
    )

    return {"hypotheses": hypotheses, "observations": observations, "log": log}
