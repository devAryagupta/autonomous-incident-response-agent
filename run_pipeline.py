from __future__ import annotations

import argparse

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.graph import GRAPH


def _print_one(incident: CrashLoopBackOffIncident) -> None:
    state = IncidentState(
        incident_id=incident.incident_id,
        created_at=incident.created_at,
        phase="ingest",
        alert=Alert(
            alert_name=incident.alert.alert_name,
            severity="critical",
            starts_at=incident.alert.starts_at,
            labels=incident.alert.labels,
            annotations=incident.alert.annotations,
        ),
        observations=Observations(logs=list(incident.logs), events=list(incident.events)),
    )
    state.observations.extra["top_n"] = 3
    state.observations.extra["target_ref"] = f"{incident.target.kind}/{incident.target.name}"

    state = GRAPH.invoke(state)

    print(f"Incident: {incident.incident_id}")
    print()

    print("Diagnosis:")
    if state.diagnosis:
        print(state.diagnosis.category or state.diagnosis.summary)
        if state.diagnosis.evidence:
            print("Evidence:")
            for e in state.diagnosis.evidence:
                print(f"- {e.text}")
    else:
        print("<none>")
    print()

    print("Hypotheses:")
    for i, h in enumerate(state.hypotheses, start=1):
        print(f"{i}.")
        print(h.description)
        print(f"Confidence: {h.likelihood:.2f}")
        if h.evidence:
            print("Evidence:")
            for e in h.evidence:
                print(f"- {e.text}")
        print()

    if state.evidence_requests:
        print("Evidence Requests:")
        for req in state.evidence_requests:
            print(f"- {req.type}: {req.query} target={req.target}")
            if req.rationale:
                print(f"  why: {req.rationale}")
        print()

    if state.evidence_results:
        print("Collected Evidence:")
        for res in state.evidence_results:
            print(f"- [{res.type}] {res.query}: {res.summary}")
        print()

    if state.hypothesis_verifications:
        print("Hypothesis Verification:")
        for v in state.hypothesis_verifications:
            delta = f"{v.confidence_delta:+.2f}"
            print(f"- {v.hypothesis}: {v.result} (delta {delta})")
            if v.required_evidence:
                print(f"  required: {', '.join(v.required_evidence)}")
            if v.supporting_evidence:
                print(f"  supports: {', '.join(v.supporting_evidence)}")
            if v.contradicting_evidence:
                print(f"  contradicts: {', '.join(v.contradicting_evidence)}")
            if v.expected_evidence:
                print(f"  expected: {', '.join(v.expected_evidence)}")
            if v.observed_required_evidence:
                print(f"  observed_required: {', '.join(v.observed_required_evidence)}")
            if v.observed_supporting_evidence:
                print(f"  observed_support: {', '.join(v.observed_supporting_evidence)}")
            if v.observed_contradicting_evidence:
                print(
                    "  observed_contradict: "
                    f"{', '.join(v.observed_contradicting_evidence)}"
                )
            if v.observed_evidence:
                print(f"  observed: {', '.join(v.observed_evidence)}")
        print()

    if state.remediation_options:
        print("Remediation Options:")
        for i, opt in enumerate(state.remediation_options, start=1):
            marker = " (chosen)" if opt.option_id == state.chosen_remediation_id else ""
            print(f"{i}. {opt.action}{marker}")
            print(f"   effect: {opt.expected_effect}")
            print(
                f"   risk={opt.risk.value} blast_radius={opt.blast_radius} "
                f"reversibility={opt.reversibility} rollback_possible={opt.rollback_possible}"
            )
            print(f"   confidence={opt.confidence:.2f} safety_score={opt.safety_score:.2f}")
        print()

    print("Fix Plan:")
    if state.fix_plan and state.fix_plan.actions:
        print(f"Risk: {state.fix_plan.risk.value}")
        for a in state.fix_plan.actions:
            change = a.params.get("change") if isinstance(a.params, dict) else None
            label = change or a.action_type.value
            print(f"- {label}")
    else:
        print("<none>")
    print()

    print("Validation:")
    if state.validation_verdict:
        print("PASS" if state.validation_verdict.passed else "FAIL")
        print(state.validation_verdict.reason)
    else:
        print("<none>")
    print()

    print("Confidence:")
    print(f"{state.confidence_score:.2f}" if state.confidence_score is not None else "<none>")
    print()

    if state.execution_plan:
        print("Execution Plan:")
        print(f"action: {state.execution_plan.action}")
        print(f"target: {state.execution_plan.target}")
        print(f"preconditions: {state.execution_plan.preconditions}")
        print(f"expected_outcome: {state.execution_plan.expected_outcome}")
        print(f"rollback_action: {state.execution_plan.rollback_action}")
        print()

    if state.approval:
        print("Approval:")
        print(f"approved={state.approval.approved} by={state.approval.by}")
        print(state.approval.comment or "")
        print()

    if state.execution:
        print("Execution:")
        print(f"status={state.execution.status} action={state.execution.action}")
        for change in state.execution.applied_changes:
            print(f"- {change}")
        print()

    if state.outcome_verification:
        print("Outcome Verification:")
        print(f"resolved={state.outcome_verification.resolved}")
        print(f"observed: {state.outcome_verification.observed_outcome}")
        print(f"unmet: {state.outcome_verification.unmet_expectations}")
        print(state.outcome_verification.reason)
        print()

    print("Incident resolved:")
    print(state.incident_resolved)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run deterministic pipeline over a dataset (no AI).")
    ap.add_argument(
        "--dataset",
        default="data/synthetic/crashloopbackoff/incidents.jsonl",
        help="CrashLoopBackOff dataset JSONL path",
    )
    ap.add_argument("--limit", type=int, default=1, help="How many incidents to print")
    args = ap.parse_args(argv)

    incidents = load_jsonl(CrashLoopBackOffIncident, args.dataset, strict=True)
    for inc in incidents[: args.limit]:
        _print_one(inc)
        print("\n" + ("-" * 60) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

