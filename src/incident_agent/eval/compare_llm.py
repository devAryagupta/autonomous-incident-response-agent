"""Compare an LLM-assisted golden run against a frozen deterministic baseline."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.eval.failures import failure_histogram
from incident_agent.eval.freeze_baseline import DEFAULT_GOLDEN_DATASET, _ensure_parent, _sha256
from incident_agent.eval.llm_metrics import (
    LLMRunTotals,
    aggregate_llm_snapshots,
    totals_to_dict,
)
from incident_agent.eval.run_golden import (
    GoldenReasoningTrace,
    GoldenRunRow,
    run_golden_dataset_with_reasoning,
    write_golden_report,
    write_reasoning_trace_report,
)
from incident_agent.llm import OpenAILLMSuggestionProvider
from incident_agent.llm.provider import LLMSuggestionProvider
from incident_agent.providers import default_providers


def summarize_rows(rows: list[GoldenRunRow]) -> dict[str, Any]:
    total = len(rows) if rows else 1
    failures = failure_histogram([row.failure_category for row in rows])
    diag = sum(1 for row in rows if row.diagnosis_correct)
    hyp = sum(1 for row in rows if row.top_hypothesis_correct)
    fix = sum(1 for row in rows if row.fix_correct)
    return {
        "incidents": len(rows),
        "diagnosis_correct": diag,
        "diagnosis_accuracy": diag / total,
        "top_hypothesis_correct": hyp,
        "top_hypothesis_accuracy": hyp / total,
        "fix_correct": fix,
        "fix_accuracy": fix / total,
        "insufficient_evidence": int(failures.get("INSUFFICIENT EVIDENCE", 0)),
        "failure_histogram": failures,
    }


def summarize_llm(traces: list[GoldenReasoningTrace]) -> LLMRunTotals:
    snapshots = [
        snap
        for trace in traces
        for pass_item in trace.reasoning_passes
        for snap in (pass_item.llm_hypothesize, pass_item.llm_collect_evidence)
        if snap is not None
    ]
    return aggregate_llm_snapshots(snapshots)


def _fraction(correct: int, total: int) -> str:
    return f"{correct}/{total}"


def _pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.0%}"


def render_comparison_markdown(manifest: dict[str, Any]) -> str:
    det = manifest["deterministic"]
    llm = manifest["llm_assisted"]
    llm_metrics = llm["llm_metrics"]
    total = int(llm["incidents"])
    det_total = int(det["incidents"])
    assisted = str(llm_metrics.get("model") or "LLM-assisted")
    lines = [
        f"# LLM Comparison — {manifest['label']}",
        "",
        f"Created at: `{manifest['created_at']}`",
        "",
        "The LLM is advisory only. Diagnosis stays deterministic, so diagnosis",
        "accuracy is not expected to improve. Success is a safer or more useful",
        "investigation trail — not a jump from 5/9 to 9/9 fix accuracy.",
        "",
        "## Scorecard",
        "",
        f"| Metric | Deterministic | {assisted} |",
        "|---|---|---|",
        "| Diagnosis accuracy | "
        f"{_fraction(det['diagnosis_correct'], det_total)} | "
        f"{_fraction(llm['diagnosis_correct'], total)} |",
        "| Top hypothesis | "
        f"{_fraction(det['top_hypothesis_correct'], det_total)} | "
        f"{_fraction(llm['top_hypothesis_correct'], total)} |",
        "| Fix accuracy | "
        f"{_fraction(det['fix_correct'], det_total)} | "
        f"{_fraction(llm['fix_correct'], total)} |",
        "| Insufficient evidence | "
        f"{_fraction(det['insufficient_evidence'], det_total)} | "
        f"{_fraction(llm['insufficient_evidence'], total)} |",
        f"| LLM acceptance | N/A | {_pct(llm_metrics.get('acceptance_rate'))} |",
        f"| Unsafe suggestions | N/A | {llm_metrics.get('unsafe_control_suggestions', 0)} |",
        f"| Unsafe accepted | N/A | {llm_metrics.get('unsafe_accepted', 0)} |",
        "",
        "## LLM activity",
        "",
        f"- provider: `{llm_metrics.get('provider') or 'unknown'}`",
        f"- model: `{llm_metrics.get('model') or 'unknown'}`",
        f"- suggestions generated: `{llm_metrics.get('suggestions_generated', 0)}`",
        f"- suggestions accepted: `{llm_metrics.get('suggestions_accepted', 0)}`",
        f"- suggestions rejected: `{llm_metrics.get('suggestions_rejected', 0)}`",
        f"- useful evidence requests: `{llm_metrics.get('useful_evidence_requests', 0)}`",
        f"- unsafe/control suggestions: `{llm_metrics.get('unsafe_control_suggestions', 0)}`",
        "",
        f"### Failure Histogram ({assisted})",
        "",
    ]
    failures = llm.get("failure_histogram") or {}
    if failures:
        lines.extend(f"- `{name}`: {count}" for name, count in failures.items())
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Per-incident",
            "",
            "| Incident | Diag | Top hyp | Fix | Failure | LLM gen/acc/rej | Useful evidence |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in manifest["incidents"]:
        lines.append(
            f"| `{row['incident_id']}` | {row['diagnosis_correct']} | "
            f"{row['top_hypothesis_correct']} | {row['fix_correct']} | "
            f"{row['failure_category'] or ''} | "
            f"{row['llm_generated']}/{row['llm_accepted']}/{row['llm_rejected']} | "
            f"{row['useful_evidence_requests']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            manifest["interpretation"],
            "",
            "## Provenance",
            "",
            f"- baseline_label: `{manifest['baseline_label']}`",
            f"- baseline_manifest: `{manifest['baseline_manifest']}`",
            f"- dataset_path: `{manifest['provenance']['dataset_path']}`",
            f"- dataset_sha256: `{manifest['provenance']['dataset_sha256']}`",
            f"- llm_run_json: `{llm['artifacts']['run_json']}`",
            f"- llm_trace_json: `{llm['artifacts']['trace_json']}`",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def interpret_comparison(*, deterministic: dict[str, Any], llm: dict[str, Any]) -> str:
    det_fix = int(deterministic["fix_correct"])
    llm_fix = int(llm["fix_correct"])
    det_diag = int(deterministic["diagnosis_correct"])
    llm_diag = int(llm["diagnosis_correct"])
    accepted = int(llm["llm_metrics"]["suggestions_accepted"])
    generated = int(llm["llm_metrics"]["suggestions_generated"])
    unsafe_accepted = int(llm["llm_metrics"]["unsafe_accepted"])
    useful = int(llm["llm_metrics"]["useful_evidence_requests"])

    parts: list[str] = []
    if llm_diag == det_diag:
        parts.append(
            "Diagnosis accuracy stayed the same. That is expected: the model "
            "does not own diagnosis."
        )
    else:
        parts.append(
            "Diagnosis accuracy moved even though the LLM is not the diagnoser. "
            "Inspect traces before treating that as a model win or loss."
        )

    if llm_fix > det_fix:
        parts.append(
            f"Fix accuracy rose from {det_fix} to {llm_fix}. Advisory suggestions "
            "changed a downstream outcome without taking control."
        )
    elif llm_fix == det_fix:
        parts.append(
            f"Fix accuracy stayed at {det_fix}/{deterministic['incidents']}. "
            f"The LLM added {useful} useful evidence request(s) but did not "
            "change the downstream outcome."
        )
    else:
        parts.append(
            f"Fix accuracy fell from {det_fix} to {llm_fix}. That is a legitimate "
            "result: it shows why deterministic verification and safety gates "
            "must stay around smaller models."
        )

    if generated:
        parts.append(
            f"Contract acceptance was {accepted}/{generated} "
            f"({accepted / generated:.0%}); unsafe accepted = {unsafe_accepted}."
        )
    else:
        parts.append(
            "The model generated no usable suggestions. Check provider warnings "
            "in the trace before interpreting acceptance."
        )
    return " ".join(parts)


def _incident_rows(
    rows: list[GoldenRunRow],
    traces: list[GoldenReasoningTrace],
) -> list[dict[str, Any]]:
    traces_by_id = {trace.incident_id: trace for trace in traces}
    out: list[dict[str, Any]] = []
    for row in rows:
        totals = (
            traces_by_id[row.incident_id].llm_metrics
            if row.incident_id in traces_by_id
            else None
        )
        out.append(
            {
                "incident_id": row.incident_id,
                "diagnosis_correct": row.diagnosis_correct,
                "top_hypothesis_correct": row.top_hypothesis_correct,
                "fix_correct": row.fix_correct,
                "failure_category": row.failure_category,
                "llm_generated": totals.suggestions_generated if totals else 0,
                "llm_accepted": totals.suggestions_accepted if totals else 0,
                "llm_rejected": totals.suggestions_rejected if totals else 0,
                "useful_evidence_requests": (
                    totals.useful_evidence_requests if totals else 0
                ),
            }
        )
    return out


def compare_llm_against_baseline(
    *,
    dataset_path: str,
    baseline_manifest: Path,
    label: str,
    artifacts_dir: str,
    timeout_seconds: float,
    llm: LLMSuggestionProvider | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    baseline = json.loads(baseline_manifest.read_text(encoding="utf-8"))
    incidents = load_jsonl(CrashLoopBackOffIncident, dataset_path, strict=True)

    deterministic_rows, _ = run_golden_dataset_with_reasoning(
        incidents,
        providers=default_providers(),
    )
    det_summary = summarize_rows(deterministic_rows)
    frozen = baseline.get("deterministic_stage0") or {}
    if frozen:
        det_summary["diagnosis_correct"] = int(
            frozen.get("diagnosis_correct", det_summary["diagnosis_correct"])
        )
        det_summary["diagnosis_accuracy"] = float(
            frozen.get("diagnosis_accuracy", det_summary["diagnosis_accuracy"])
        )
        det_summary["fix_correct"] = int(frozen.get("fix_correct", det_summary["fix_correct"]))
        det_summary["fix_accuracy"] = float(frozen.get("fix_accuracy", det_summary["fix_accuracy"]))
        det_summary["incidents"] = int(frozen.get("incidents", det_summary["incidents"]))
        hist = frozen.get("failure_histogram") or {}
        det_summary["insufficient_evidence"] = int(
            hist.get("INSUFFICIENT EVIDENCE", det_summary["insufficient_evidence"])
        )
        det_summary["failure_histogram"] = hist or det_summary["failure_histogram"]

    overrides: dict[str, Any] = {"timeout_seconds": timeout_seconds}
    if model:
        overrides["model"] = model
    provider = llm or OpenAILLMSuggestionProvider.from_env(**overrides)
    llm_rows, llm_traces = run_golden_dataset_with_reasoning(
        incidents,
        providers=default_providers(llm=provider),
    )
    llm_summary = summarize_rows(llm_rows)
    llm_totals = summarize_llm(llm_traces)

    out_dir = Path(artifacts_dir)
    run_json = out_dir / "golden_llm_run.json"
    run_csv = out_dir / "golden_llm_run.csv"
    trace_json = out_dir / "golden_llm_trace.json"
    write_golden_report(llm_rows, out_json=run_json, out_csv=run_csv)
    write_reasoning_trace_report(llm_traces, out_json=trace_json)

    llm_summary["llm_metrics"] = totals_to_dict(llm_totals)
    llm_summary["artifacts"] = {
        "run_json": str(run_json),
        "run_csv": str(run_csv),
        "trace_json": str(trace_json),
    }

    return {
        "schema_version": "1",
        "label": label,
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "baseline_label": baseline.get("label"),
        "baseline_manifest": str(baseline_manifest),
        "deterministic": det_summary,
        "llm_assisted": llm_summary,
        "incidents": _incident_rows(llm_rows, llm_traces),
        "interpretation": interpret_comparison(
            deterministic=det_summary,
            llm=llm_summary,
        ),
        "provenance": {
            "dataset_path": dataset_path,
            "dataset_sha256": _sha256(Path(dataset_path)),
            "llm_provider": llm_totals.provider,
            "llm_model": llm_totals.model,
        },
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Run the frozen golden set with an env-configured LLM and compare."
    )
    ap.add_argument("--dataset", default=DEFAULT_GOLDEN_DATASET)
    ap.add_argument(
        "--baseline",
        default="docs/baselines/2026-09-07.json",
        help="Frozen deterministic baseline manifest",
    )
    ap.add_argument(
        "--label",
        default=f"{datetime.now(tz=UTC).date().isoformat()}-qwen",
        help="Snapshot label (default: today's UTC date + -qwen)",
    )
    ap.add_argument(
        "--model",
        default=None,
        help="Override LLM_MODEL / provider default for this run only",
    )
    ap.add_argument("--artifacts-dir", default=None)
    ap.add_argument("--manifest-json", default=None)
    ap.add_argument("--manifest-md", default=None)
    ap.add_argument(
        "--timeout-seconds",
        type=float,
        default=45.0,
        help="Per-request LLM timeout (default: 45)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    label = str(args.label)
    artifacts_dir = args.artifacts_dir or f"artifacts/baselines/{label}"
    manifest_json = Path(args.manifest_json or f"docs/baselines/{label}.json")
    manifest_md = Path(args.manifest_md or f"docs/baselines/{label}.md")

    manifest = compare_llm_against_baseline(
        dataset_path=args.dataset,
        baseline_manifest=Path(args.baseline),
        label=label,
        artifacts_dir=artifacts_dir,
        timeout_seconds=float(args.timeout_seconds),
        model=args.model,
    )

    _ensure_parent(manifest_json)
    _ensure_parent(manifest_md)
    manifest_json.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest_md.write_text(render_comparison_markdown(manifest), encoding="utf-8")

    det = manifest["deterministic"]
    llm = manifest["llm_assisted"]
    metrics = llm["llm_metrics"]
    print("LLM comparison complete")
    print(f"- label: {label}")
    print(
        f"- diagnosis: {det['diagnosis_correct']}/{det['incidents']} -> "
        f"{llm['diagnosis_correct']}/{llm['incidents']}"
    )
    print(
        f"- top hypothesis: {det['top_hypothesis_correct']}/{det['incidents']} -> "
        f"{llm['top_hypothesis_correct']}/{llm['incidents']}"
    )
    print(
        f"- fix: {det['fix_correct']}/{det['incidents']} -> "
        f"{llm['fix_correct']}/{llm['incidents']}"
    )
    print(f"- llm accepted: {metrics['suggestions_accepted']}/{metrics['suggestions_generated']}")
    print(f"- unsafe accepted: {metrics['unsafe_accepted']}")
    print(f"- artifacts_dir: {artifacts_dir}")
    print(f"- manifest_json: {manifest_json}")
    print(f"- manifest_md: {manifest_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
