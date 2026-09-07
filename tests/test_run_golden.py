"""Tests for golden-dataset Stage-0 batch runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from incident_agent.contracts import LLMSuggestion, LLMSuggestionRequest, LLMSuggestionResponse
from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import (
    Alert,
    CrashLoopBackOffIncident,
    ExpectedFix,
    K8sRef,
)
from incident_agent.eval.run_golden import (
    GoldenReasoningTrace,
    GoldenRunRow,
    run_golden_dataset,
    run_golden_dataset_with_reasoning,
    run_incident_with_reasoning_trace,
    state_from_incident,
    write_golden_report,
    write_reasoning_trace_report,
)
from incident_agent.providers import default_providers


def _oom_incident() -> CrashLoopBackOffIncident:
    now = datetime(2026, 8, 29, 10, 0, 0, tzinfo=UTC)
    return CrashLoopBackOffIncident(
        incident_id="cl-oom-test",
        created_at=now,
        target=K8sRef(namespace="payments", kind="Deployment", name="order-service"),
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=now),
        logs=[
            "Out of memory: Kill process 1 (java) score 1021 or sacrifice child",
            "java.lang.OutOfMemoryError: Java heap space",
            "Exit Code: 137",
        ],
        events=[
            "Warning OOMKilling pod/order-service Reason: OOMKilled, Exit Code: 137",
        ],
        category="oom",
        root_cause="Container exceeded memory limit (Exit Code: 137).",
        expected_fix=ExpectedFix(
            summary="Raise memory limit",
            kind="increase_memory_limit",
        ),
    )


def _missing_env_incident() -> CrashLoopBackOffIncident:
    now = datetime(2026, 8, 29, 11, 0, 0, tzinfo=UTC)
    return CrashLoopBackOffIncident(
        incident_id="cl-missing-env-test",
        created_at=now,
        target=K8sRef(namespace="notifications", kind="Deployment", name="email-worker"),
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=now),
        logs=[
            "Traceback (most recent call last):",
            "KeyError: 'DATABASE_URL'",
            "missing environment variable DATABASE_URL",
            "Exit Code: 1",
        ],
        events=[
            "Normal Started pod/email-worker Started container email-worker",
            "Warning BackOff pod/email-worker Back-off restarting failed container",
        ],
        category="missing_env_var",
        root_cause=(
            "Invalid Configuration: required environment variable DATABASE_URL is "
            "missing, causing KeyError at startup."
        ),
        expected_fix=ExpectedFix(
            summary="Patch deployment to include DATABASE_URL from Secret/ConfigMap.",
            kind="patch_env_var",
        ),
    )


def test_state_from_incident_sets_target_ref() -> None:
    inc = _oom_incident()
    state = state_from_incident(inc)
    assert state.observations.extra["target_ref"] == "Deployment/order-service"
    assert state.observations.logs == inc.logs


def test_run_golden_dataset_one_incident() -> None:
    rows = run_golden_dataset([_oom_incident()])
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, GoldenRunRow)
    assert row.incident_id == "cl-oom-test"
    assert row.expected_fix_kind == "increase_memory_limit"
    assert row.validation_result in {"PASS", "FAIL"}
    assert row.final_confidence is not None


def test_run_golden_dataset_missing_env_prefers_patch_env_var() -> None:
    rows, traces = run_golden_dataset_with_reasoning([_missing_env_incident()])
    assert len(rows) == 1
    row = rows[0]
    last_pass = traces[0].reasoning_passes[-1]
    # Planner still prefers the config fix; gate 0.673 < 0.7 so it is not committed.
    assert last_pass.selected_remediation == "patch_env_var"
    assert last_pass.route_decision == "escalate"
    assert row.predicted_fix_kind is None
    assert row.expected_fix_kind == "patch_env_var"
    assert row.fix_correct is False
    assert row.failure_category == "INSUFFICIENT EVIDENCE"


def test_run_golden_dataset_with_reasoning_one_incident() -> None:
    rows, traces = run_golden_dataset_with_reasoning([_oom_incident()])
    assert len(rows) == 1
    assert len(traces) == 1
    trace = traces[0]
    assert isinstance(trace, GoldenReasoningTrace)
    assert trace.reasoning_passes
    one_pass = trace.reasoning_passes[0]
    assert one_pass.initial_hypotheses
    assert one_pass.evidence_requests
    assert one_pass.evidence_results
    assert one_pass.verification_results
    assert one_pass.posterior_hypotheses
    assert one_pass.remediation_options
    assert one_pass.selected_remediation


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
                        rationale="Restart loop plus heap growth.",
                    )
                ],
            )
        return LLMSuggestionResponse(
            provider="scripted",
            model="mock-v1",
            stage=request.stage,
            suggestions=[
                LLMSuggestion(
                    suggestion_id="s-ev-good",
                    stage="collect_evidence",
                    kind="evidence",
                    summary="request previous_container_logs",
                    rationale="Need prior crash logs.",
                    metadata={
                        "query": "previous_container_logs",
                        "type": "log",
                        "target": request.target_ref or "Deployment/order-service",
                    },
                ),
                LLMSuggestion(
                    suggestion_id="s-ev-bad",
                    stage="collect_evidence",
                    kind="evidence",
                    summary="execute rollout_restart immediately",
                    rationale="just fix it",
                ),
            ],
        )


def test_golden_runner_passes_llm_provider_into_hypothesize() -> None:
    bundle = default_providers(llm=_ScriptedLLMProvider())
    _state, trace = run_incident_with_reasoning_trace(_oom_incident(), providers=bundle)
    first = trace.reasoning_passes[0]
    assert first.llm_hypothesize is not None
    assert first.llm_hypothesize.generated_count == 1
    assert first.llm_hypothesize.accepted_count == 1
    assert any(
        item.description == "Memory leak is likely" for item in first.initial_hypotheses
    )
    assert first.llm_collect_evidence is not None
    rejected = [
        item for item in first.llm_collect_evidence.suggestions if not item.accepted
    ]
    assert rejected
    assert rejected[0].rejection_reason in {
        "control_plane_intent",
        "forbidden_control_field",
    }
    assert "rollout_restart" in rejected[0].summary
    assert trace.llm_metrics is not None
    assert trace.llm_metrics.unsafe_accepted == 0
    assert isinstance(trace.top_hypothesis_correct, bool)


def test_write_golden_report(tmp_path: Path) -> None:
    row = GoldenRunRow(
        incident_id="x",
        predicted_root_cause="OOMKilled",
        expected_root_cause="OOM",
        diagnosis_correct=True,
        predicted_fix_kind="increase_memory_limit",
        expected_fix_kind="increase_memory_limit",
        fix_correct=True,
        validation_result="PASS",
        final_confidence=0.8,
    )
    out_json = tmp_path / "report.json"
    out_csv = tmp_path / "report.csv"
    write_golden_report([row], out_json=out_json, out_csv=out_csv)
    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded[0]["incident_id"] == "x"
    assert "incident_id" in out_csv.read_text(encoding="utf-8")


def test_write_reasoning_trace_report(tmp_path: Path) -> None:
    _rows, traces = run_golden_dataset_with_reasoning([_oom_incident()])
    out_json = tmp_path / "trace.json"
    write_reasoning_trace_report(traces, out_json=out_json)
    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded["trace_count"] == 1
    assert loaded["traces"][0]["incident_id"] == "cl-oom-test"
    assert loaded["traces"][0]["reasoning_passes"][0]["evidence_requests"]


def test_golden_file_on_disk_has_nine_rows() -> None:
    path = Path("data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset.jsonl")
    if not path.exists():
        return
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 9


def test_case_008_no_self_confirmation_and_stable_replans() -> None:
    incidents = load_jsonl(
        CrashLoopBackOffIncident,
        "data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset.jsonl",
        strict=True,
    )
    incident = next(i for i in incidents if i.incident_id == "cl-app-startup-failure-008")
    _state, trace = run_incident_with_reasoning_trace(incident)

    assert trace.expected_hypothesis == "Fatal runtime error"
    assert trace.predicted_hypothesis == "Fatal runtime error"
    assert len(trace.reasoning_passes) >= 2

    unhandled_observed_per_pass: list[list[str]] = []
    for reasoning_pass in trace.reasoning_passes:
        by_hyp = {v.hypothesis: v for v in reasoning_pass.verification_results}

        fatal = by_hyp["Fatal runtime error"]
        assert fatal.result == "confirmed"
        assert any(
            ("panic" in item.lower()) or ("fatal error" in item.lower())
            for item in fatal.observed_evidence
        )

        unhandled = by_hyp["Unhandled exception in application"]
        assert unhandled.result == "inconclusive"
        assert "Unhandled exception or traceback appears in logs" in unhandled.expected_evidence
        assert not unhandled.observed_required_evidence
        assert not unhandled.observed_supporting_evidence
        assert all(
            "application raised an unhandled exception" not in item.lower()
            for item in unhandled.observed_evidence
        )
        unhandled_observed_per_pass.append(list(unhandled.observed_evidence))

        startup = by_hyp["Startup regression after deploy"]
        assert startup.result == "inconclusive"
        assert "Deploy/revision change signal is present" in startup.expected_evidence
        assert not startup.observed_required_evidence

    assert all(not items for items in unhandled_observed_per_pass)
