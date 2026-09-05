"""Run Stage-0 agent over CrashLoop golden JSONL and record reasoning traces."""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from incident_agent.contracts import (
    EvidenceRequest,
    EvidenceResult,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    RemediationOption,
)
from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.eval.metrics import causes_match
from incident_agent.nodes import (
    approve,
    collect_evidence,
    compute_confidence,
    diagnose,
    execute_fix,
    hypothesize,
    plan_fix,
    pre_execute_validate,
    prepare_execution,
    validate_fix,
    verify_hypotheses,
    verify_outcome,
)
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import ProviderBundle, default_providers
from incident_agent.routing import (
    INSUFFICIENT_CONFIDENCE,
    NOOP_DECISION,
    bump_replan,
    escalate_insufficient_confidence,
    finalize,
    route_on_confidence,
)

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


@dataclass(frozen=True, slots=True)
class HypothesisSnapshot:
    hypothesis_id: str
    description: str
    # Current belief (prior or posterior depending on snapshot timing).
    likelihood: float
    remediation_key: str | None


@dataclass(frozen=True, slots=True)
class EvidenceRequestSnapshot:
    request_id: str
    hypothesis_id: str | None
    type: str
    query: str
    target: str
    rationale: str


@dataclass(frozen=True, slots=True)
class EvidenceResultSnapshot:
    request_id: str
    type: str
    query: str
    success: bool
    summary: str
    data: dict


@dataclass(frozen=True, slots=True)
class VerificationSnapshot:
    hypothesis_id: str
    hypothesis: str
    result: str
    supporting_evidence: list[str]
    contradicting_evidence: list[str]
    required_evidence: list[str]
    observed_supporting_evidence: list[str]
    observed_contradicting_evidence: list[str]
    observed_required_evidence: list[str]
    expected_evidence: list[str]
    observed_evidence: list[str]
    confidence_delta: float


@dataclass(frozen=True, slots=True)
class RemediationOptionSnapshot:
    option_id: str
    action: str
    hypothesis_id: str | None
    # Heuristic suitability, not P(success).
    confidence: float
    safety_score: float
    risk: str
    blast_radius: str
    reversibility: str
    rollback_possible: bool


@dataclass(frozen=True, slots=True)
class ReasoningPassSnapshot:
    pass_index: int
    route_decision: Literal["execute", "replan", "escalate"]
    initial_hypotheses: list[HypothesisSnapshot]
    evidence_requests: list[EvidenceRequestSnapshot]
    evidence_results: list[EvidenceResultSnapshot]
    verification_results: list[VerificationSnapshot]
    posterior_hypotheses: list[HypothesisSnapshot]
    selected_hypothesis_id: str | None
    selected_hypothesis: str | None
    remediation_options: list[RemediationOptionSnapshot]
    selected_remediation_id: str | None
    selected_remediation: str | None
    confidence_score: float | None
    confidence_explanation: str | None


@dataclass(frozen=True, slots=True)
class GoldenReasoningTrace:
    incident_id: str
    scenario_category: str
    diagnosis_category: str | None
    diagnosis_summary: str | None
    diagnosis_confidence: float | None
    diagnosis_evidence: list[str]
    expected_root_cause: str
    expected_hypothesis: str
    expected_fix_kind: str | None
    predicted_hypothesis: str | None
    predicted_fix_kind: str | None
    diagnosis_correct: bool
    fix_correct: bool
    final_confidence: float | None
    replan_count: int
    route: str | None
    reasoning_passes: list[ReasoningPassSnapshot]


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
    from incident_agent.contracts import Alert, Observations

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
    if state.decision == NOOP_DECISION or state.decision_reason == INSUFFICIENT_CONFIDENCE:
        return None
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


def _apply_updates(state: IncidentState, updates: dict[str, object]) -> None:
    for key, value in updates.items():
        setattr(state, key, value)


def _hypothesis_snapshots(hypotheses: list[Hypothesis]) -> list[HypothesisSnapshot]:
    return [
        HypothesisSnapshot(
            hypothesis_id=h.hypothesis_id,
            description=h.description,
            likelihood=float(h.likelihood),
            remediation_key=h.remediation_key,
        )
        for h in hypotheses
    ]


def _request_snapshots(requests: list[EvidenceRequest]) -> list[EvidenceRequestSnapshot]:
    return [
        EvidenceRequestSnapshot(
            request_id=r.request_id,
            hypothesis_id=r.hypothesis_id,
            type=str(r.type),
            query=r.query,
            target=r.target,
            rationale=r.rationale,
        )
        for r in requests
    ]


def _result_snapshots(results: list[EvidenceResult]) -> list[EvidenceResultSnapshot]:
    return [
        EvidenceResultSnapshot(
            request_id=r.request_id,
            type=str(r.type),
            query=r.query,
            success=bool(r.success),
            summary=r.summary,
            data=dict(r.data),
        )
        for r in results
    ]


def _verification_snapshots(
    verifications: list[HypothesisVerification],
) -> list[VerificationSnapshot]:
    return [
        VerificationSnapshot(
            hypothesis_id=v.hypothesis_id,
            hypothesis=v.hypothesis,
            result=v.result,
            supporting_evidence=list(v.supporting_evidence),
            contradicting_evidence=list(v.contradicting_evidence),
            required_evidence=list(v.required_evidence),
            observed_supporting_evidence=list(v.observed_supporting_evidence),
            observed_contradicting_evidence=list(v.observed_contradicting_evidence),
            observed_required_evidence=list(v.observed_required_evidence),
            expected_evidence=list(v.expected_evidence),
            observed_evidence=list(v.observed_evidence),
            confidence_delta=float(v.confidence_delta),
        )
        for v in verifications
    ]


def _remediation_option_snapshots(
    options: list[RemediationOption],
) -> list[RemediationOptionSnapshot]:
    return [
        RemediationOptionSnapshot(
            option_id=o.option_id,
            action=o.action,
            hypothesis_id=o.hypothesis_id,
            confidence=float(o.confidence),
            safety_score=float(o.safety_score),
            risk=o.risk.value,
            blast_radius=str(o.blast_radius),
            reversibility=str(o.reversibility),
            rollback_possible=bool(o.rollback_possible),
        )
        for o in options
    ]


def _selected_hypothesis_description(state: IncidentState) -> str | None:
    if not state.chosen_hypothesis_id:
        return None
    for hyp in state.hypotheses:
        if hyp.hypothesis_id == state.chosen_hypothesis_id:
            return hyp.description
    return None


def _selected_remediation_action(state: IncidentState) -> str | None:
    if not state.chosen_remediation_id:
        return None
    for opt in state.remediation_options:
        if opt.option_id == state.chosen_remediation_id:
            return opt.action
    return None


def infer_expected_hypothesis(incident: CrashLoopBackOffIncident) -> str:
    """
    Approximate scenario-authored hypothesis label from category + root-cause prose.

    The golden schema currently stores root-cause narrative + expected fix, but not
    a dedicated hypothesis field.
    """
    category = incident.category
    root = incident.root_cause.lower()
    if category == "oom":
        if "leak" in root:
            return "Memory leak"
        if "traffic spike" in root or "load" in root or "rps" in root:
            return "Traffic spike"
        return "Memory limit too low"
    if category == "missing_env_var":
        return "Missing environment variable"
    if category == "bad_config":
        return "Bad configuration"
    if category == "missing_secret":
        return "Secret not created"
    if category in {"startup_exception", "app_bug_unhandled_exception"}:
        if "panic" in root or "fatal" in root:
            return "Fatal runtime error"
        if "startup" in root:
            return "Startup regression after deploy"
        return "Unhandled exception in application"
    return str(category)


def run_incident_with_reasoning_trace(
    incident: CrashLoopBackOffIncident,
    *,
    providers: ProviderBundle | None = None,
) -> tuple[IncidentState, GoldenReasoningTrace]:
    """Run full lifecycle and capture pass-by-pass reasoning artifacts."""
    bundle = providers or default_providers()
    state = state_from_incident(incident)

    _apply_updates(state, enrich(state, providers=bundle))
    _apply_updates(state, diagnose(state))

    passes: list[ReasoningPassSnapshot] = []
    pass_index = 0
    while True:
        pass_index += 1
        _apply_updates(state, hypothesize(state))
        initial_hypotheses = _hypothesis_snapshots(list(state.hypotheses))

        _apply_updates(state, collect_evidence(state, providers=bundle))
        evidence_requests = _request_snapshots(list(state.evidence_requests))
        evidence_results = _result_snapshots(list(state.evidence_results))

        _apply_updates(state, verify_hypotheses(state))
        verification_results = _verification_snapshots(list(state.hypothesis_verifications))
        posterior_hypotheses = _hypothesis_snapshots(list(state.hypotheses))

        _apply_updates(state, plan_fix(state))
        remediation_options = _remediation_option_snapshots(list(state.remediation_options))

        _apply_updates(state, validate_fix(state))
        _apply_updates(state, compute_confidence(state))
        decision = route_on_confidence(state)

        passes.append(
            ReasoningPassSnapshot(
                pass_index=pass_index,
                route_decision=decision,
                initial_hypotheses=initial_hypotheses,
                evidence_requests=evidence_requests,
                evidence_results=evidence_results,
                verification_results=verification_results,
                posterior_hypotheses=posterior_hypotheses,
                selected_hypothesis_id=state.chosen_hypothesis_id,
                selected_hypothesis=_selected_hypothesis_description(state),
                remediation_options=remediation_options,
                selected_remediation_id=state.chosen_remediation_id,
                selected_remediation=_selected_remediation_action(state),
                confidence_score=_final_confidence(state),
                confidence_explanation=(
                    state.confidence.explanation if state.confidence is not None else None
                ),
            )
        )

        if decision == "replan":
            _apply_updates(state, bump_replan(state))
            continue
        if decision == "escalate":
            _apply_updates(state, escalate_insufficient_confidence(state))
            _apply_updates(state, finalize(state, providers=bundle))
            break
        _apply_updates(state, prepare_execution(state))
        _apply_updates(state, pre_execute_validate(state))
        _apply_updates(state, approve(state))
        _apply_updates(state, execute_fix(state, providers=bundle))
        _apply_updates(state, verify_outcome(state, providers=bundle))
        _apply_updates(state, finalize(state, providers=bundle))
        break

    row = row_from_run(incident, state)
    trace = GoldenReasoningTrace(
        incident_id=incident.incident_id,
        scenario_category=str(incident.category),
        diagnosis_category=state.diagnosis.category if state.diagnosis else None,
        diagnosis_summary=state.diagnosis.summary if state.diagnosis else None,
        diagnosis_confidence=(
            float(state.diagnosis.confidence) if state.diagnosis is not None else None
        ),
        diagnosis_evidence=(
            [item.text for item in state.diagnosis.evidence] if state.diagnosis else []
        ),
        expected_root_cause=incident.root_cause,
        expected_hypothesis=infer_expected_hypothesis(incident),
        expected_fix_kind=incident.expected_fix.kind,
        predicted_hypothesis=_selected_hypothesis_description(state),
        predicted_fix_kind=row.predicted_fix_kind,
        diagnosis_correct=row.diagnosis_correct,
        fix_correct=row.fix_correct,
        final_confidence=row.final_confidence,
        replan_count=state.replan_count,
        route=state.route,
        reasoning_passes=passes,
    )
    return state, trace


def run_golden_dataset(
    incidents: list[CrashLoopBackOffIncident],
) -> list[GoldenRunRow]:
    """Run Stage-0 lifecycle over each incident and return summary rows."""
    rows, _ = run_golden_dataset_with_reasoning(incidents)
    return rows


def run_golden_dataset_with_reasoning(
    incidents: list[CrashLoopBackOffIncident],
    *,
    providers: ProviderBundle | None = None,
) -> tuple[list[GoldenRunRow], list[GoldenReasoningTrace]]:
    """Run full lifecycle over each incident and return (summary rows, traces)."""
    bundle = providers or default_providers()
    rows: list[GoldenRunRow] = []
    traces: list[GoldenReasoningTrace] = []
    for incident in incidents:
        state, trace = run_incident_with_reasoning_trace(incident, providers=bundle)
        rows.append(row_from_run(incident, state))
        traces.append(trace)
    return rows, traces


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


def write_reasoning_trace_report(
    traces: list[GoldenReasoningTrace],
    *,
    out_json: Path,
) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "trace_count": len(traces),
        "traces": [asdict(trace) for trace in traces],
    }
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run Stage-0 agent over CrashLoop golden JSONL and record "
            "diagnosis/fix metrics plus full reasoning traces."
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
        help="Where to write summary JSON report",
    )
    ap.add_argument(
        "--out-csv",
        default="artifacts/benchmarks/golden_stage0_run.csv",
        help="Where to write summary CSV report (empty to skip)",
    )
    ap.add_argument(
        "--out-trace-json",
        default="artifacts/benchmarks/golden_stage0_trace.json",
        help="Where to write full reasoning trace JSON",
    )
    args = ap.parse_args(argv)

    incidents = load_jsonl(CrashLoopBackOffIncident, args.dataset, strict=True)
    rows, traces = run_golden_dataset_with_reasoning(incidents)

    out_json = Path(args.out_json)
    out_csv = Path(args.out_csv) if args.out_csv else None
    out_trace_json = Path(args.out_trace_json)
    write_golden_report(rows, out_json=out_json, out_csv=out_csv)
    write_reasoning_trace_report(traces, out_json=out_trace_json)

    n = len(rows)
    diag_ok = sum(1 for r in rows if r.diagnosis_correct)
    fix_ok = sum(1 for r in rows if r.fix_correct)
    print(f"Ran {n} incidents from {args.dataset}")
    print(f"diagnosis_correct: {diag_ok}/{n}")
    print(f"fix_correct:       {fix_ok}/{n}")
    print(f"JSON: {out_json}")
    if out_csv:
        print(f"CSV:  {out_csv}")
    print(f"TRACE: {out_trace_json}")
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
