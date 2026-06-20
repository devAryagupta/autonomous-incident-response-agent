from __future__ import annotations

import argparse

from incident_agent.contracts import Alert
from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.contracts import IncidentState, Observations
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
    print(state.diagnosis.summary if state.diagnosis else "<none>")
    print()

    print("Hypotheses:")
    for i, h in enumerate(state.hypotheses, start=1):
        print(f"{i}. {h.description} ({h.likelihood:.2f})")
    print()

    print("Fix Plan:")
    if state.fix_plan and state.fix_plan.actions:
        for a in state.fix_plan.actions:
            print(f"- {a.action_type.value}: {a.target}")
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

