from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from incident_agent.datasets.core import DatasetLoadError, load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident


class HypothesisPrediction(BaseModel):
    cause: str


class CrashLoopPrediction(BaseModel):
    """
    Minimal contract for agent outputs we can evaluate today.
    Extend later without breaking old datasets by versioning this schema.
    """

    schema_version: Literal["1"] = "1"
    incident_id: str
    predicted_category: str | None = None
    predicted_root_cause: str | None = None
    predicted_fix_summary: str | None = None
    predicted_hypotheses: list[HypothesisPrediction] = []
    predicted_fix_kind: str | None = None
    predicted_diagnosis: str | None = None


@dataclass(frozen=True, slots=True)
class EvalCounts:
    total: int
    category_correct: int
    root_cause_nonempty: int
    fix_nonempty: int
    hypothesis_top1_correct: int
    hypothesis_top3_correct: int
    fix_kind_correct: int
    diagnosis_correct: int


@dataclass(frozen=True, slots=True)
class EvalReport:
    counts: EvalCounts
    category_accuracy: float
    hypothesis_top1_accuracy: float
    hypothesis_top3_accuracy: float
    fix_kind_accuracy: float
    diagnosis_accuracy: float


def _normalize_text(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _canonical_cause_from_category(category: str) -> str:
    # dataset category -> hypothesis "cause" name (human readable)
    mapping = {
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


def _hypothesis_hit(pred: CrashLoopPrediction, expected_cause: str, *, k: int) -> bool:
    expected = _normalize_text(expected_cause)
    ranked = pred.predicted_hypotheses[:k]
    return any(_normalize_text(h.cause) == expected for h in ranked if h.cause)


def _fix_kind_hit(pred: CrashLoopPrediction, expected_kind: str | None) -> bool:
    if not expected_kind:
        return False
    if not pred.predicted_fix_kind:
        return False
    return _normalize_text(pred.predicted_fix_kind) == _normalize_text(expected_kind)


def evaluate_crashloop(
    incidents: list[CrashLoopBackOffIncident],
    predictions: list[CrashLoopPrediction],
) -> EvalReport:
    pred_by_id = {p.incident_id: p for p in predictions}

    total = len(incidents)
    cat_ok = 0
    root_nonempty = 0
    fix_nonempty = 0
    hyp_top1_ok = 0
    hyp_top3_ok = 0
    fix_kind_ok = 0
    diagnosis_ok = 0

    for inc in incidents:
        p = pred_by_id.get(inc.incident_id)
        if not p:
            continue

        if p.predicted_category and _normalize_text(p.predicted_category) == _normalize_text(
            inc.category
        ):
            cat_ok += 1
        if p.predicted_root_cause and _normalize_text(p.predicted_root_cause):
            root_nonempty += 1
        if p.predicted_fix_summary and _normalize_text(p.predicted_fix_summary):
            fix_nonempty += 1

        expected_cause = _canonical_cause_from_category(inc.category)
        if _hypothesis_hit(p, expected_cause, k=1):
            hyp_top1_ok += 1
        if _hypothesis_hit(p, expected_cause, k=3):
            hyp_top3_ok += 1

        if _fix_kind_hit(p, inc.expected_fix.kind):
            fix_kind_ok += 1

        # Baseline: "diagnosis accuracy" = exact string match vs dataset root_cause.
        if p.predicted_diagnosis and _normalize_text(p.predicted_diagnosis) == _normalize_text(
            inc.root_cause
        ):
            diagnosis_ok += 1

    counts = EvalCounts(
        total=total,
        category_correct=cat_ok,
        root_cause_nonempty=root_nonempty,
        fix_nonempty=fix_nonempty,
        hypothesis_top1_correct=hyp_top1_ok,
        hypothesis_top3_correct=hyp_top3_ok,
        fix_kind_correct=fix_kind_ok,
        diagnosis_correct=diagnosis_ok,
    )
    category_accuracy = (cat_ok / total) if total else 0.0
    hypothesis_top1_accuracy = (hyp_top1_ok / total) if total else 0.0
    hypothesis_top3_accuracy = (hyp_top3_ok / total) if total else 0.0
    fix_kind_accuracy = (fix_kind_ok / total) if total else 0.0
    diagnosis_accuracy = (diagnosis_ok / total) if total else 0.0
    return EvalReport(
        counts=counts,
        category_accuracy=category_accuracy,
        hypothesis_top1_accuracy=hypothesis_top1_accuracy,
        hypothesis_top3_accuracy=hypothesis_top3_accuracy,
        fix_kind_accuracy=fix_kind_accuracy,
        diagnosis_accuracy=diagnosis_accuracy,
    )


def _render_report(report: EvalReport) -> str:
    c = report.counts
    return "\n".join(
        [
            "Dataset evaluation report",
            f"- total_incidents: {c.total}",
            f"- category_accuracy: {report.category_accuracy:.3f}",
            f"- hypothesis_top1_accuracy: {report.hypothesis_top1_accuracy:.3f}",
            f"- hypothesis_top3_accuracy: {report.hypothesis_top3_accuracy:.3f}",
            f"- fix_kind_accuracy: {report.fix_kind_accuracy:.3f}",
            f"- diagnosis_accuracy: {report.diagnosis_accuracy:.3f}",
            f"- predicted_root_cause_nonempty: {c.root_cause_nonempty}/{c.total}",
            f"- predicted_fix_nonempty: {c.fix_nonempty}/{c.total}",
        ]
    )


def _load_predictions_jsonl(path: str | Path) -> list[CrashLoopPrediction]:
    p = Path(path)
    preds: list[CrashLoopPrediction] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            preds.append(CrashLoopPrediction.model_validate(json.loads(s)))
    return preds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate agent predictions against a dataset (JSONL)."
    )
    ap.add_argument("--dataset", required=True, help="Dataset JSONL (CrashLoopBackOffIncident)")
    ap.add_argument("--predictions", required=True, help="Predictions JSONL (CrashLoopPrediction)")
    args = ap.parse_args(argv)

    try:
        incidents = load_jsonl(CrashLoopBackOffIncident, args.dataset, strict=True)
    except DatasetLoadError as e:
        print(str(e))
        return 2

    predictions = _load_predictions_jsonl(args.predictions)
    report = evaluate_crashloop(incidents, predictions)
    print(_render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

