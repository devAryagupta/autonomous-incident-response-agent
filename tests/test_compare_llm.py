from __future__ import annotations

import json
from pathlib import Path

from incident_agent.contracts import LLMSuggestion, LLMSuggestionRequest, LLMSuggestionResponse
from incident_agent.datasets.core import write_jsonl
from incident_agent.datasets.crashloopbackoff.generate import make_incident
from incident_agent.eval.compare_llm import (
    compare_llm_against_baseline,
    interpret_comparison,
    render_comparison_markdown,
    summarize_rows,
)
from incident_agent.eval.run_golden import GoldenRunRow


class _QuietLLM:
    def suggest(self, request: LLMSuggestionRequest) -> LLMSuggestionResponse:
        return LLMSuggestionResponse(
            provider="scripted",
            model="mock-v1",
            stage=request.stage,
            suggestions=[
                LLMSuggestion(
                    suggestion_id=f"s-{request.stage}-1",
                    stage=request.stage,  # type: ignore[arg-type]
                    kind="hypothesis" if request.stage == "hypothesize" else "evidence",
                    summary=(
                        "Memory leak is likely"
                        if request.stage == "hypothesize"
                        else "request previous_container_logs"
                    ),
                    rationale="scripted",
                    metadata=(
                        {}
                        if request.stage == "hypothesize"
                        else {
                            "query": "previous_container_logs",
                            "type": "log",
                            "target": request.target_ref or "workload",
                        }
                    ),
                )
            ],
        )


def test_interpret_same_fix_accuracy_is_legitimate() -> None:
    text = interpret_comparison(
        deterministic={
            "incidents": 9,
            "diagnosis_correct": 9,
            "fix_correct": 5,
        },
        llm={
            "incidents": 9,
            "diagnosis_correct": 9,
            "fix_correct": 5,
            "llm_metrics": {
                "suggestions_generated": 8,
                "suggestions_accepted": 6,
                "unsafe_accepted": 0,
                "useful_evidence_requests": 2,
            },
        },
    )
    assert "stayed at 5/9" in text
    assert "did not change the downstream outcome" in text


def test_compare_llm_against_scripted_provider(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    incidents = [make_incident(seed=7, idx=0)]
    write_jsonl(incidents, dataset_path)
    baseline = {
        "label": "test-baseline",
        "deterministic_stage0": {
            "incidents": 1,
            "diagnosis_correct": 1,
            "diagnosis_accuracy": 1.0,
            "fix_correct": 0,
            "fix_accuracy": 0.0,
            "failure_histogram": {"INSUFFICIENT EVIDENCE": 1},
        },
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    manifest = compare_llm_against_baseline(
        dataset_path=str(dataset_path),
        baseline_manifest=baseline_path,
        label="test-qwen",
        artifacts_dir=str(tmp_path / "artifacts"),
        timeout_seconds=1.0,
        llm=_QuietLLM(),
    )
    assert manifest["deterministic"]["incidents"] == 1
    assert manifest["llm_assisted"]["llm_metrics"]["suggestions_generated"] >= 1
    assert Path(manifest["llm_assisted"]["artifacts"]["trace_json"]).exists()
    markdown = render_comparison_markdown(manifest)
    assert "LLM acceptance" in markdown
    assert "Deterministic" in markdown


def test_summarize_rows_counts_top_hypothesis() -> None:
    rows = [
        GoldenRunRow(
            incident_id="a",
            predicted_root_cause="OOM",
            expected_root_cause="OOM",
            diagnosis_correct=True,
            predicted_fix_kind="increase_memory_limit",
            expected_fix_kind="increase_memory_limit",
            fix_correct=True,
            validation_result="PASS",
            final_confidence=0.8,
            top_hypothesis_correct=True,
        ),
        GoldenRunRow(
            incident_id="b",
            predicted_root_cause="OOM",
            expected_root_cause="OOM",
            diagnosis_correct=True,
            predicted_fix_kind=None,
            expected_fix_kind="patch_env_var",
            fix_correct=False,
            validation_result=None,
            final_confidence=0.5,
            failure_category="INSUFFICIENT EVIDENCE",
            top_hypothesis_correct=False,
        ),
    ]
    summary = summarize_rows(rows)
    assert summary["diagnosis_correct"] == 2
    assert summary["top_hypothesis_correct"] == 1
    assert summary["fix_correct"] == 1
    assert summary["insufficient_evidence"] == 1
