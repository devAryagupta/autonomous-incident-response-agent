"""Run Stage-0 agent over CrashLoop golden JSONL and record baseline metrics."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.eval.metrics import causes_match
from incident_agent.graph import GRAPH

DEFAULT_GOLDEN_DATASET = (
    "data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset.jsonl"
)


@dataclass(frozen=True, slots=True)
class GoldenRunRow:
    incident_id: str
    predicted_root_cause: str | None
    expected_root_cause: str
    diagnosis_correct: bool
    predicted_fix_kind: str | None
    expected_fix_kind: str | None
    fix_correct: bool
    validation_result: str | None
    final_confidence: float | None


def _normalize_kind(value: str | None) -> str:
    if not value:
        return ""
    return value.strip().lower().replace("_", " ")


def _fix_kinds_match(predicted: str | None, expected: str | None) -> bool:
    if not predicted or not expected:
        return False
    return _normalize_kind(predicted) == _normalize_kind(expected)


def state_from_incident(incident: CrashLoopBackOffIncident) -> IncidentState:
    """Build initial IncidentState the same way as ``run_pipeline.py``."""
    state = IncidentState(
        incident_id=incident.incident_id,
        created_at=incident.created_at,
        phase="ingest",
        alert=Alert(
            alert_name=incident.alert.alert_name,
            severity=incident.alert.severity,
            starts_at=incident.alert.starts_at,
            labels=dict(incident.alert.labels),
            annotations=dict(incident.alert.annotations),
        ),
        observations=Observations(logs=list(incident.logs), events=list(incident.events)),
    )
    state.observations.extra["top_n"] = 3
    state.observations.extra["target_ref"] = f"{incident.target.kind}/{incident.target.name}"
    return state


def _predicted_root_cause(state: IncidentState) -> str | None:
    if state.diagnosis is None:
        return None
    return state.diagnosis.summary or state.diagnosis.category


def _predicted_fix_kind(state: IncidentState) -> str | None:
    if state.chosen_remediation_id and state.remediation_options:
        for opt in state.remediation_options:
            if opt.option_id == state.chosen_remediation_id:
                return opt.action
    if state.fix_plan and state.fix_plan.actions:
        first = state.fix_plan.actions[0]
        params = first.params if isinstance(first.params, dict) else {}
        return params.get("action") or first.action_type.value
    return None


def _validation_result(state: IncidentState) -> str | None:
    if state.validation_verdict is None:
        return None
    return "PASS" if state.validation_verdict.passed else "FAIL"


def _final_confidence(state: IncidentState) -> float | None:
    if state.confidence_score is not None:
        return float(state.confidence_score)
    if state.confidence is not None:
        return float(state.confidence.score)
    return None


def row_from_run(incident: CrashLoopBackOffIncident, state: IncidentState) -> GoldenRunRow:
    predicted_root = _predicted_root_cause(state)
    expected_root = incident.root_cause
    diagnosis_category = state.diagnosis.category if state.diagnosis else None
    diagnosis_correct = causes_match(predicted_root, expected_root) or causes_match(
        diagnosis_category, expected_root
    )

    predicted_fix = _predicted_fix_kind(state)
    expected_fix = incident.expected_fix.kind

    return GoldenRunRow(
        incident_id=incident.incident_id,
        predicted_root_cause=predicted_root,
        expected_root_cause=expected_root,
        diagnosis_correct=diagnosis_correct,
        predicted_fix_kind=predicted_fix,
        expected_fix_kind=expected_fix,
        fix_correct=_fix_kinds_match(predicted_fix, expected_fix),
        validation_result=_validation_result(state),
        final_confidence=_final_confidence(state),
    )


def run_golden_dataset(
    incidents: list[CrashLoopBackOffIncident],
) -> list[GoldenRunRow]:
    """Run Stage-0 GRAPH over each incident; do not mutate agent logic."""
    rows: list[GoldenRunRow] = []
    for incident in incidents:
        state = GRAPH.invoke(state_from_incident(incident))
        rows.append(row_from_run(incident, state))
    return rows


def write_golden_report(rows: list[GoldenRunRow], *, out_json: Path, out_csv: Path | None) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) for r in rows]
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if out_csv is not None:
        fieldnames = list(asdict(rows[0]).keys()) if rows else []
        with out_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run Stage-0 agent (GRAPH) over CrashLoop golden JSONL and record "
            "diagnosis/fix/validation/confidence per incident."
        )
    )
    ap.add_argument(
        "--dataset",
        default=DEFAULT_GOLDEN_DATASET,
        help="Golden CrashLoopBackOff JSONL path",
    )
    ap.add_argument(
        "--out-json",
        default="artifacts/benchmarks/golden_stage0_run.json",
        help="Where to write JSON report",
    )
    ap.add_argument(
        "--out-csv",
        default="artifacts/benchmarks/golden_stage0_run.csv",
        help="Where to write CSV report (empty to skip)",
    )
    args = ap.parse_args(argv)

    incidents = load_jsonl(CrashLoopBackOffIncident, args.dataset, strict=True)
    rows = run_golden_dataset(incidents)

    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv) if args.out_csv else None
    write_golden_report(rows, out_json=out_json, out_csv=out_csv)

    n = len(rows)
    diag_ok = sum(1 for r in rows if r.diagnosis_correct)
    fix_ok = sum(1 for r in rows if r.fix_correct)
    print(f"Ran {n} incidents from {args.dataset}")
    print(f"diagnosis_correct: {diag_ok}/{n}")
    print(f"fix_correct:       {fix_ok}/{n}")
    print(f"JSON: {out_json}")
    if out_csv:
        print(f"CSV:  {out_csv}")
    print()
    for row in rows:
        print(
            f"{row.incident_id}  "
            f"diag={row.diagnosis_correct}  "
            f"fix={row.fix_correct}  "
            f"val={row.validation_result}  "
            f"conf={row.final_confidence}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
