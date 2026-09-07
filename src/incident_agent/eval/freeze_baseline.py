"""Freeze deterministic baseline artifacts and a commit-friendly manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incident_agent.benchmark import run_crashloop_benchmark
from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.eval.failures import failure_histogram
from incident_agent.eval.run_golden import (
    run_golden_dataset_with_reasoning,
    write_golden_report,
    write_reasoning_trace_report,
)

DEFAULT_GOLDEN_DATASET = (
    "data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset.jsonl"
)


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _git_output(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()
    except Exception:
        return "<unknown>"


def _git_is_dirty() -> bool:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        return bool(proc.stdout.strip())
    except Exception:
        return True


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _render_markdown(manifest: dict[str, Any]) -> str:
    det = manifest["deterministic_stage0"]
    bench = manifest["heuristic_benchmark"]
    failures = det["failure_histogram"]
    failure_items = (
        "\n".join(f"- `{name}`: {count}" for name, count in failures.items())
        if failures
        else "- none"
    )
    return (
        f"# Baseline Freeze — {manifest['label']}\n\n"
        f"Created at: `{manifest['created_at']}`\n\n"
        "## Deterministic Stage-0\n\n"
        f"- incidents: `{det['incidents']}`\n"
        f"- diagnosis_correct: `{det['diagnosis_correct']}/{det['incidents']}`\n"
        f"- diagnosis_accuracy: `{det['diagnosis_accuracy']:.4f}`\n"
        f"- top_hypothesis_correct: `{det.get('top_hypothesis_correct', '?')}/{det['incidents']}`\n"
        f"- top_hypothesis_accuracy: `{float(det.get('top_hypothesis_accuracy') or 0):.4f}`\n"
        f"- fix_correct: `{det['fix_correct']}/{det['incidents']}`\n"
        f"- fix_accuracy: `{det['fix_accuracy']:.4f}`\n"
        f"- mean_final_confidence: `{det['mean_final_confidence']:.4f}`\n\n"
        "### Failure Histogram\n\n"
        f"{failure_items}\n\n"
        "## Heuristic Benchmark\n\n"
        f"- category_accuracy: `{bench['category_accuracy']:.4f}`\n"
        f"- diagnosis_accuracy: `{bench['diagnosis_accuracy']:.4f}`\n"
        f"- top1_hypothesis_accuracy: `{bench['hypothesis_top1_accuracy']:.4f}`\n"
        f"- top3_hypothesis_accuracy: `{bench['hypothesis_top3_accuracy']:.4f}`\n"
        f"- fix_kind_accuracy: `{bench['fix_kind_accuracy']:.4f}`\n"
        f"- validation_pass_rate: `{bench['validation_pass_rate']:.4f}`\n\n"
        "## Provenance\n\n"
        f"- git_commit: `{manifest['provenance']['git_commit']}`\n"
        f"- git_branch: `{manifest['provenance']['git_branch']}`\n"
        f"- git_dirty: `{manifest['provenance']['git_dirty']}`\n"
        f"- python_version: `{manifest['provenance']['python_version']}`\n"
        f"- dataset_path: `{manifest['provenance']['dataset_path']}`\n"
        f"- dataset_sha256: `{manifest['provenance']['dataset_sha256']}`\n"
        f"- golden_run_json: `{det['artifacts']['run_json']}`\n"
        f"- golden_trace_json: `{det['artifacts']['trace_json']}`\n"
        f"- heuristic_report_json: `{bench['artifacts']['report_json']}`\n"
    )


def freeze_stage0_baseline(
    *,
    dataset_path: str,
    label: str,
    artifacts_dir: str,
) -> dict[str, Any]:
    dataset = Path(dataset_path)
    incidents = load_jsonl(CrashLoopBackOffIncident, dataset, strict=True)
    rows, traces = run_golden_dataset_with_reasoning(incidents)

    out_dir = Path(artifacts_dir)
    run_json = out_dir / "golden_stage0_run.json"
    run_csv = out_dir / "golden_stage0_run.csv"
    trace_json = out_dir / "golden_stage0_trace.json"
    write_golden_report(rows, out_json=run_json, out_csv=run_csv)
    write_reasoning_trace_report(traces, out_json=trace_json)

    diag_correct = sum(1 for row in rows if row.diagnosis_correct)
    hyp_correct = sum(1 for row in rows if row.top_hypothesis_correct)
    fix_correct = sum(1 for row in rows if row.fix_correct)
    confidences = [row.final_confidence for row in rows if row.final_confidence is not None]
    confidence_mean = (sum(confidences) / len(confidences)) if confidences else 0.0
    failures = failure_histogram([row.failure_category for row in rows])
    total = len(rows) if rows else 1

    benchmark_report, benchmark_artifacts = run_crashloop_benchmark(
        dataset_path=str(dataset),
        baseline="heuristic",
        out_dir=str(out_dir / "heuristic"),
    )

    return {
        "schema_version": "1",
        "label": label,
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "deterministic_stage0": {
            "incidents": len(rows),
            "diagnosis_correct": diag_correct,
            "diagnosis_accuracy": diag_correct / total,
            "top_hypothesis_correct": hyp_correct,
            "top_hypothesis_accuracy": hyp_correct / total,
            "fix_correct": fix_correct,
            "fix_accuracy": fix_correct / total,
            "mean_final_confidence": confidence_mean,
            "failure_histogram": failures,
            "artifacts": {
                "run_json": str(run_json),
                "run_csv": str(run_csv),
                "trace_json": str(trace_json),
            },
        },
        "heuristic_benchmark": {
            "category_accuracy": benchmark_report.category_accuracy,
            "diagnosis_accuracy": benchmark_report.diagnosis_accuracy,
            "hypothesis_top1_accuracy": benchmark_report.hypothesis_top1_accuracy,
            "hypothesis_top3_accuracy": benchmark_report.hypothesis_top3_accuracy,
            "fix_kind_accuracy": benchmark_report.fix_kind_accuracy,
            "validation_pass_rate": benchmark_report.validation_pass_rate,
            "confidence_calibration_ece": benchmark_report.confidence_calibration_ece,
            "artifacts": {
                "predictions_jsonl": benchmark_artifacts.predictions_path,
                "report_json": benchmark_artifacts.report_path,
            },
        },
        "provenance": {
            "git_commit": _git_output("rev-parse", "HEAD"),
            "git_branch": _git_output("rev-parse", "--abbrev-ref", "HEAD"),
            "git_dirty": _git_is_dirty(),
            "python_version": platform.python_version(),
            "dataset_path": str(dataset),
            "dataset_sha256": _sha256(dataset),
        },
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=(
            "Freeze today's deterministic baseline so future LLM runs can "
            "be compared against an explicit snapshot."
        )
    )
    ap.add_argument(
        "--dataset",
        default=DEFAULT_GOLDEN_DATASET,
        help="Golden CrashLoopBackOff dataset JSONL path",
    )
    ap.add_argument(
        "--label",
        default=datetime.now(tz=UTC).date().isoformat(),
        help="Snapshot label (default: today's UTC date)",
    )
    ap.add_argument(
        "--artifacts-dir",
        default=None,
        help="Where to write generated run artifacts (default: artifacts/baselines/<label>)",
    )
    ap.add_argument(
        "--manifest-json",
        default=None,
        help=(
            "Where to write the commit-friendly manifest JSON "
            "(default: docs/baselines/<label>.json)"
        ),
    )
    ap.add_argument(
        "--manifest-md",
        default=None,
        help=(
            "Where to write the human-readable manifest Markdown "
            "(default: docs/baselines/<label>.md)"
        ),
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    label = str(args.label)
    artifacts_dir = args.artifacts_dir or f"artifacts/baselines/{label}"
    manifest_json = Path(args.manifest_json or f"docs/baselines/{label}.json")
    manifest_md = Path(args.manifest_md or f"docs/baselines/{label}.md")

    manifest = freeze_stage0_baseline(
        dataset_path=args.dataset,
        label=label,
        artifacts_dir=artifacts_dir,
    )

    _ensure_parent(manifest_json)
    _ensure_parent(manifest_md)
    manifest_json.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest_md.write_text(_render_markdown(manifest), encoding="utf-8")

    print("Baseline freeze complete")
    print(f"- label: {label}")
    det = manifest["deterministic_stage0"]
    bench = manifest["heuristic_benchmark"]
    print(f"- deterministic diagnosis_accuracy: {det['diagnosis_accuracy']:.3f}")
    print(f"- deterministic fix_accuracy: {det['fix_accuracy']:.3f}")
    print(f"- heuristic category_accuracy: {bench['category_accuracy']:.3f}")
    print(f"- artifacts_dir: {artifacts_dir}")
    print(f"- manifest_json: {manifest_json}")
    print(f"- manifest_md: {manifest_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
