from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from incident_agent.datasets.core import DatasetLoadError, load_jsonl, write_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.datasets.eval import (
    CrashLoopPrediction,
    EvalReport,
    HypothesisPrediction,
    evaluate_crashloop,
)

BenchmarkTask = Literal["crashloopbackoff"]
Baseline = Literal["oracle", "empty", "heuristic"]


class CrashLoopPredictor(Protocol):
    def predict(self, incident: CrashLoopBackOffIncident) -> CrashLoopPrediction: ...


class OracleCrashLoopPredictor:
    """Debug baseline: copies labels from the dataset (upper bound / sanity check)."""

    def predict(self, incident: CrashLoopBackOffIncident) -> CrashLoopPrediction:
        return CrashLoopPrediction(
            incident_id=incident.incident_id,
            predicted_category=incident.category,
            predicted_root_cause=incident.root_cause,
            predicted_fix_summary=incident.expected_fix.summary,
            predicted_fix_kind=incident.expected_fix.kind,
            predicted_diagnosis=incident.root_cause,
            predicted_hypotheses=[
                HypothesisPrediction(cause=_canonical_cause(incident.category), likelihood=1.0)
            ],
            predicted_validation_passed=True,
            predicted_confidence_score=1.0,
        )


class EmptyCrashLoopPredictor:
    """Lower bound: emits no predictions (tests evaluator/reporting plumbing)."""

    def predict(self, incident: CrashLoopBackOffIncident) -> CrashLoopPrediction:
        return CrashLoopPrediction(incident_id=incident.incident_id)


class HeuristicCrashLoopPredictor:
    """Tiny keyword baseline (no AI) to validate end-to-end benchmark flow."""

    def predict(self, incident: CrashLoopBackOffIncident) -> CrashLoopPrediction:
        joined = "\n".join(incident.logs + incident.events).lower()

        if "oomkilled" in joined or "killed" == joined.strip():
            category = "resource_constraint_oom"
            diagnosis = "Container OOMKilled during startup"
            fix = "Increase memory requests/limits"
        elif "configmap" in joined and "not found" in joined:
            category = "missing_configmap"
            diagnosis = "Referenced ConfigMap missing"
            fix = "Create or reference correct ConfigMap"
        elif "secret" in joined and "not found" in joined:
            category = "missing_secret"
            diagnosis = "Referenced Secret missing"
            fix = "Create secret or fix secret reference"
        elif "no such host" in joined or "lookup" in joined:
            category = "dependency_unavailable"
            diagnosis = "Dependency unreachable (DNS/network)"
            fix = "Restore dependency / fix Service DNS"
        elif "environment variable missing" in joined:
            category = "missing_env_var"
            diagnosis = "Required environment variable missing"
            fix = "Patch deployment env var"
        else:
            category = None
            diagnosis = None
            fix = None

        return CrashLoopPrediction(
            incident_id=incident.incident_id,
            predicted_category=category,
            predicted_root_cause=diagnosis,
            predicted_fix_summary=fix,
            predicted_fix_kind=(
                "rollback_deployment"
                if category == "app_bug_unhandled_exception"
                else None
            ),
            predicted_diagnosis=diagnosis,
            predicted_hypotheses=(
                [HypothesisPrediction(cause=diagnosis)] if diagnosis else []
            ),
        )


def _canonical_cause(category: str) -> str:
    # Keep in sync with datasets.eval canonicalization.
    mapping = {
        # Invalid Image
        "invalid_image_wrong_tag": "Invalid Image Tag / Image Pull Error",
        "invalid_image_deleted_image": "Invalid Image Tag / Image Pull Error",
        "invalid_image_private_registry_auth": "Invalid Image Tag / Image Pull Error",
        # Application Failure
        "bad_config": "Bad Configuration / Config Parse Error",
        "startup_exception": "Application Bug / Unhandled Exception",
        # Resource Failure
        "oom": "Resource Constraint (OOMKilled)",
        "disk_pressure": "Resource Constraint (Disk Pressure / No Space)",
        "cpu_starvation": "Resource Constraint (CPU Starvation)",
        # Dependency Failure
        "dependency_database_unavailable": "Database Unavailable",
        "dependency_redis_unavailable": "Redis Unavailable",
        "dependency_dns_failure": "DNS Resolution Failure",

        # Legacy
        "missing_secret": "Missing Secret",
        "missing_env_var": "Missing Environment Variable",
        "missing_configmap": "Missing ConfigMap",
        "dependency_unavailable": "Dependency Unavailable (DNS/Network)",
        "app_bug_unhandled_exception": "Application Bug / Unhandled Exception",
        "resource_constraint_oom": "Resource Constraint (OOMKilled)",
        "resource_constraint_cpu": "Resource Constraint (CPU Starvation)",
        "misconfigured_volume_mount": "Misconfigured Volume Mount / Missing Path",
        "bad_env_var_value": "Bad Environment Variable Value",
    }
    return mapping.get(category, category)


@dataclass(frozen=True, slots=True)
class BenchmarkArtifacts:
    dataset_path: str
    predictions_path: str
    report_path: str


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_report(report: EvalReport, *, out_path: Path, meta: dict) -> None:
    payload = {
        "schema_version": "1",
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "meta": meta,
        "report": {
            "total_incidents": report.counts.total,
            "predictions_found": report.counts.predictions_found,
            "category_accuracy": report.category_accuracy,
            "hypothesis_top1_accuracy": report.hypothesis_top1_accuracy,
            "hypothesis_top3_accuracy": report.hypothesis_top3_accuracy,
            "fix_kind_accuracy": report.fix_kind_accuracy,
            "diagnosis_accuracy": report.diagnosis_accuracy,
            "validation_pass_rate": report.validation_pass_rate,
            "average_confidence": report.average_confidence,
            "confidence_calibration_ece": report.confidence_calibration_ece,
            "predicted_root_cause_nonempty": report.counts.root_cause_nonempty,
            "predicted_fix_nonempty": report.counts.fix_nonempty,
        },
    }
    _ensure_parent(out_path)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_crashloop_benchmark(
    *,
    dataset_path: str,
    baseline: Baseline,
    out_dir: str,
) -> tuple[EvalReport, BenchmarkArtifacts]:
    incidents = load_jsonl(CrashLoopBackOffIncident, dataset_path, strict=True)

    predictor: CrashLoopPredictor
    if baseline == "oracle":
        predictor = OracleCrashLoopPredictor()
    elif baseline == "empty":
        predictor = EmptyCrashLoopPredictor()
    else:
        predictor = HeuristicCrashLoopPredictor()

    started = time.perf_counter()
    preds = [predictor.predict(inc) for inc in incidents]
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    out = Path(out_dir)
    preds_path = out / f"predictions.{baseline}.jsonl"
    report_path = out / f"report.{baseline}.json"
    _ensure_parent(preds_path)
    write_jsonl(preds, preds_path)

    report = evaluate_crashloop(incidents, preds)
    _write_report(
        report,
        out_path=report_path,
        meta={
            "task": "crashloopbackoff",
            "baseline": baseline,
            "dataset_path": str(Path(dataset_path)),
            "elapsed_ms": elapsed_ms,
        },
    )

    return report, BenchmarkArtifacts(
        dataset_path=str(Path(dataset_path)),
        predictions_path=str(preds_path),
        report_path=str(report_path),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run dataset benchmarks and emit artifacts.")
    ap.add_argument("--task", choices=["crashloopbackoff"], required=True)
    ap.add_argument("--dataset", required=True, help="Dataset JSONL path")
    ap.add_argument("--baseline", choices=["oracle", "empty", "heuristic"], default="heuristic")
    ap.add_argument("--out-dir", default="artifacts/benchmarks", help="Where to write outputs")
    args = ap.parse_args(argv)

    try:
        if args.task == "crashloopbackoff":
            report, artifacts = run_crashloop_benchmark(
                dataset_path=args.dataset,
                baseline=args.baseline,
                out_dir=args.out_dir,
            )
        else:
            raise AssertionError("Unhandled task")
    except DatasetLoadError as e:
        print(str(e))
        return 2

    print("Benchmark complete")
    print(f"- task: {args.task}")
    print(f"- baseline: {args.baseline}")
    print(f"- diagnosis_accuracy: {report.diagnosis_accuracy:.3f}")
    print(f"- top1_hypothesis_accuracy: {report.hypothesis_top1_accuracy:.3f}")
    print(f"- top3_hypothesis_accuracy: {report.hypothesis_top3_accuracy:.3f}")
    print(f"- fix_accuracy: {report.fix_kind_accuracy:.3f}")
    print(f"- validation_pass_rate: {report.validation_pass_rate:.3f}")
    if report.average_confidence is not None:
        print(f"- average_confidence: {report.average_confidence:.3f}")
    if report.confidence_calibration_ece is not None:
        print(f"- confidence_calibration_ece: {report.confidence_calibration_ece:.3f}")
    print(f"- category_accuracy: {report.category_accuracy:.3f}")
    print(f"- predictions: {artifacts.predictions_path}")
    print(f"- report: {artifacts.report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

