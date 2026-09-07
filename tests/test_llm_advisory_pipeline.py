from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    LLMSuggestion,
    LLMSuggestionRequest,
    LLMSuggestionResponse,
)
from incident_agent.pipeline import run_deterministic_lifecycle
from incident_agent.providers import (
    DryRunExecutionProvider,
    NoMemoryProvider,
    ProviderBundle,
    SyntheticMetricsProvider,
    SyntheticObservationProvider,
)


class _ScriptedLLMProvider:
    def suggest(self, request: LLMSuggestionRequest) -> LLMSuggestionResponse:
        if request.stage == "hypothesize":
            return LLMSuggestionResponse(
                provider="scripted",
                model="mock-v1",
                stage="hypothesize",
                suggestions=[
                    LLMSuggestion(
                        suggestion_id="s-hyp-1",
                        stage="hypothesize",
                        kind="hypothesis",
                        summary="Memory leak is likely",
                        rationale="Repeated restarts suggest memory growth over cycles.",
                    )
                ],
            )
        if request.stage == "collect_evidence":
            return LLMSuggestionResponse(
                provider="scripted",
                model="mock-v1",
                stage="collect_evidence",
                suggestions=[
                    LLMSuggestion(
                        suggestion_id="s-ev-good",
                        stage="collect_evidence",
                        kind="evidence",
                        summary="request previous_container_logs",
                    ),
                    LLMSuggestion(
                        suggestion_id="s-ev-bad",
                        stage="collect_evidence",
                        kind="evidence",
                        summary="execute rollout_restart immediately",
                    ),
                ],
            )
        return LLMSuggestionResponse(
            provider="scripted",
            model="mock-v1",
            stage=request.stage,
            suggestions=[],
        )


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=NoMemoryProvider(),
        llm=_ScriptedLLMProvider(),
    )


def test_pipeline_ingests_advisory_hypothesis_and_evidence_only() -> None:
    alert = Alert(
        alert_name="CrashLoopBackOff",
        severity="critical",
        starts_at=datetime.now(tz=UTC),
    )
    state = run_deterministic_lifecycle(
        incident_id="inc-llm-advisory",
        alert=alert,
        logs=[
            "Warning OOMKilled kubelet Container killed due to OOM",
            "Back-off restarting failed container",
        ],
        events=[],
        top_n=3,
        target_ref="deployment/demo-app",
        confidence_threshold=0.0,
        providers=_bundle(),
        stop_before_execution=True,
    )

    assert any(item.description == "Memory leak is likely" for item in state.hypotheses)
    assert any(
        request.query == "previous_container_logs"
        and request.request_id.startswith("er-llm-")
        for request in state.evidence_requests
    )
    assert all(request.query != "rollout_restart" for request in state.evidence_requests)
    assert state.decision is None
    assert state.decision_reason is None

    audit = state.observations.extra.get("llm_ingestion", {})
    assert "hypothesize" in audit
    assert "collect_evidence" in audit
    assert audit["collect_evidence"]["accepted_count"] == 1
    assert audit["collect_evidence"]["rejected_count"] >= 1
