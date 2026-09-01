from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from incident_agent.datasets.core import DatasetLoadError, load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident


class HypothesisPrediction(BaseModel):
    cause: str
    likelihood: float | None = Field(default=None, ge=0.0, le=1.0)


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
    predicted_hypotheses: list[HypothesisPrediction] = Field(default_factory=list)
    predicted_fix_kind: str | None = None
    predicted_diagnosis: str | None = None
    predicted_validation_passed: bool | None = None
    predicted_confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class EvalCounts:
    total: int
    predictions_found: int
    category_correct: int
    root_cause_nonempty: int
    fix_nonempty: int
    hypothesis_top1_correct: int
    hypothesis_top3_correct: int
    fix_kind_correct: int
    diagnosis_correct: int
    validation_passed: int
    confidence_present: int


@dataclass(frozen=True, slots=True)
class EvalReport:
    counts: EvalCounts
    category_accuracy: float
    hypothesis_top1_accuracy: float
    hypothesis_top3_accuracy: float
    fix_kind_accuracy: float
    diagnosis_accuracy: float
    validation_pass_rate: float
    average_confidence: float | None
    confidence_calibration_ece: float | None


def _normalize_text(s: str) -> str:
    return " ".join(s.strip().lower().split())


def _canonical_cause_from_category(category: str) -> str:
    # dataset category -> scoped root cause label.
    mapping = {
        "oom": "OOMKilled",
        "resource_constraint_oom": "OOMKilled",
        "bad_config": "Invalid Configuration",
        "missing_secret": "Invalid Configuration",
        "missing_env_var": "Invalid Configuration",
        "missing_configmap": "Invalid Configuration",
        "misconfigured_volume_mount": "Invalid Configuration",
        "bad_env_var_value": "Invalid Configuration",
        "invalid_image_wrong_tag": "Invalid Configuration",
        "invalid_image_deleted_image": "Invalid Configuration",
        "invalid_image_private_registry_auth": "Invalid Configuration",
        "invalid_image": "Invalid Configuration",
        "startup_exception": "Application Failure",
        "app_bug_unhandled_exception": "Application Failure",
        "dependency_database_unavailable": "Application Failure",
        "dependency_redis_unavailable": "Application Failure",
        "dependency_dns_failure": "Application Failure",
        "dependency_unavailable": "Application Failure",
        "disk_pressure": "Application Failure",
        "cpu_starvation": "Application Failure",
        "resource_constraint_cpu": "Application Failure",
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


def _expected_calibration_error(
    pairs: list[tuple[float, bool]],
    *,
    bins: int = 10,
) -> float | None:
    """
    Expected Calibration Error (ECE) using equal-width bins over [0, 1].

    Each pair is (confidence, correct).
    """
    if not pairs:
        return None

    if bins <= 0:
        raise ValueError("bins must be > 0")

    bucketed: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for conf, ok in pairs:
        # Clamp to be safe with any legacy data.
        c = max(0.0, min(1.0, float(conf)))
        idx = min(int(c * bins), bins - 1)  # c==1.0 -> last bin
        bucketed[idx].append((c, ok))

    n = len(pairs)
    ece = 0.0
    for bucket in bucketed:
        if not bucket:
            continue
        m = len(bucket)
        avg_conf = sum(c for c, _ in bucket) / m
        acc = sum(1.0 for _, ok in bucket if ok) / m
        ece += (m / n) * abs(acc - avg_conf)

    return float(ece)


def evaluate_crashloop(
    incidents: list[CrashLoopBackOffIncident],
    predictions: list[CrashLoopPrediction],
) -> EvalReport:
    pred_by_id = {p.incident_id: p for p in predictions}

    total = len(incidents)
    found = 0
    cat_ok = 0
    root_nonempty = 0
    fix_nonempty = 0
    hyp_top1_ok = 0
    hyp_top3_ok = 0
    fix_kind_ok = 0
    diagnosis_ok = 0
    validation_passed = 0
    confidence_present = 0
    confidence_sum = 0.0
    calibration_pairs: list[tuple[float, bool]] = []

    for inc in incidents:
        p = pred_by_id.get(inc.incident_id)
        if not p:
            continue
        found += 1

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

        fix_ok = _fix_kind_hit(p, inc.expected_fix.kind)
        if fix_ok:
            fix_kind_ok += 1

        # Baseline: "diagnosis accuracy" = exact string match vs dataset root_cause.
        if p.predicted_diagnosis and _normalize_text(p.predicted_diagnosis) == _normalize_text(
            inc.root_cause
        ):
            diagnosis_ok += 1

        if p.predicted_validation_passed is True:
            validation_passed += 1

        if p.predicted_confidence_score is not None:
            c = float(p.predicted_confidence_score)
            confidence_present += 1
            confidence_sum += c
            # Calibrate confidence against fix correctness (Fix Accuracy).
            calibration_pairs.append((c, fix_ok))

    counts = EvalCounts(
        total=total,
        predictions_found=found,
        category_correct=cat_ok,
        root_cause_nonempty=root_nonempty,
        fix_nonempty=fix_nonempty,
        hypothesis_top1_correct=hyp_top1_ok,
        hypothesis_top3_correct=hyp_top3_ok,
        fix_kind_correct=fix_kind_ok,
        diagnosis_correct=diagnosis_ok,
        validation_passed=validation_passed,
        confidence_present=confidence_present,
    )
    category_accuracy = (cat_ok / total) if total else 0.0
    hypothesis_top1_accuracy = (hyp_top1_ok / total) if total else 0.0
    hypothesis_top3_accuracy = (hyp_top3_ok / total) if total else 0.0
    fix_kind_accuracy = (fix_kind_ok / total) if total else 0.0
    diagnosis_accuracy = (diagnosis_ok / total) if total else 0.0
    validation_pass_rate = (validation_passed / total) if total else 0.0
    average_confidence = (
        (confidence_sum / confidence_present) if confidence_present else None
    )
    confidence_calibration_ece = _expected_calibration_error(calibration_pairs, bins=10)
    return EvalReport(
        counts=counts,
        category_accuracy=category_accuracy,
        hypothesis_top1_accuracy=hypothesis_top1_accuracy,
        hypothesis_top3_accuracy=hypothesis_top3_accuracy,
        fix_kind_accuracy=fix_kind_accuracy,
        diagnosis_accuracy=diagnosis_accuracy,
        validation_pass_rate=validation_pass_rate,
        average_confidence=average_confidence,
        confidence_calibration_ece=confidence_calibration_ece,
    )


def _render_report(report: EvalReport) -> str:
    c = report.counts
    avg_conf = "n/a" if report.average_confidence is None else f"{report.average_confidence:.3f}"
    ece = (
        "n/a"
        if report.confidence_calibration_ece is None
        else f"{report.confidence_calibration_ece:.3f}"
    )
    return "\n".join(
        [
            "Dataset evaluation report",
            f"- total_incidents: {c.total}",
            f"- predictions_found: {c.predictions_found}/{c.total}",
            f"- diagnosis_accuracy: {report.diagnosis_accuracy:.3f}",
            f"- top1_hypothesis_accuracy: {report.hypothesis_top1_accuracy:.3f}",
            f"- top3_hypothesis_accuracy: {report.hypothesis_top3_accuracy:.3f}",
            f"- fix_accuracy: {report.fix_kind_accuracy:.3f}",
            f"- validation_pass_rate: {report.validation_pass_rate:.3f}",
            f"- average_confidence: {avg_conf}",
            f"- confidence_calibration_ece: {ece}",
            f"- category_accuracy: {report.category_accuracy:.3f}",
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

