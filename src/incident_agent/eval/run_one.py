"""Run one ``IncidentScenario`` through a partial agent path and score it."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from incident_agent.contracts import IncidentState
from incident_agent.datasets.huggingface import (
    DatasetSplit,
    hf_row_to_scenario,
    load_devops_incident_response,
    scenarios_from_records,
)
from incident_agent.datasets.scenario import IncidentScenario
from incident_agent.eval import build_scorecard, evaluate_scenario
from incident_agent.eval.models import DecisionTrace, ScenarioResult
from incident_agent.eval.trace import decision_trace_from_state
from incident_agent.pipeline import run_deterministic_lifecycle
from incident_agent.providers import ProviderBundle, default_providers


def run_scenario_partial(
    scenario: IncidentScenario,
    *,
    include_execution: bool = False,
    providers: ProviderBundle | None = None,
    confidence_threshold: float = 0.0,
) -> tuple[IncidentState, DecisionTrace, ScenarioResult]:
    """
    Run diagnose → hypothesize → evidence → verify → plan → validate → confidence.

    By default skips execute / outcome verification (partial benchmark slice).
    Returns ``(state, trace, scenario_result)``.
    """
    started = datetime.now(tz=UTC)
    alert = scenario.to_alert(starts_at=started)
    obs = scenario.to_observations()

    state = run_deterministic_lifecycle(
        incident_id=scenario.scenario_id,
        alert=alert,
        logs=list(obs.logs),
        events=list(obs.events),
        target_ref=str(obs.extra.get("target_ref") or scenario.scenario_id),
        confidence_threshold=confidence_threshold,
        providers=providers or default_providers(),
        created_at=started,
        stop_before_execution=not include_execution,
    )
    finished = datetime.now(tz=UTC)
    trace = decision_trace_from_state(
        state,
        scenario,
        started_at=started,
        finished_at=finished,
        include_execution=include_execution,
    )
    result = evaluate_scenario(trace)
    return state, trace, result


def load_one_scenario(
    *,
    split: DatasetSplit = "train",
    scenario_id: str | None = None,
    index: int = 0,
) -> IncidentScenario:
    """Load one Hub scenario (network) by id or split index."""
    scenarios = load_devops_incident_response(split)
    if scenario_id:
        for s in scenarios:
            if s.scenario_id == scenario_id:
                return s
        known = ", ".join(s.scenario_id for s in scenarios)
        raise ValueError(f"scenario_id {scenario_id!r} not in {split}; have: {known}")
    if index < 0 or index >= len(scenarios):
        raise IndexError(f"index {index} out of range for {split} ({len(scenarios)} scenarios)")
    return scenarios[index]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Run one IncidentScenario (partial agent path), build DecisionTrace, "
            "compare to root_cause / resolution_steps, emit EvaluationScorecard."
        )
    )
    ap.add_argument("--split", default="train", choices=["train", "validation", "test"])
    ap.add_argument("--scenario-id", default=None, help="e.g. INC-2024-001")
    ap.add_argument("--index", type=int, default=0, help="Index within split if no id")
    ap.add_argument(
        "--include-execution",
        action="store_true",
        help="Also run dry-run execute + outcome verify",
    )
    ap.add_argument(
        "--fixture-json",
        default=None,
        help="Offline: path to a single HF-shaped JSON object (skips Hub download)",
    )
    ap.add_argument("--out", default=None, help="Optional path to write scorecard JSON")
    args = ap.parse_args(argv)

    if args.fixture_json:
        with open(args.fixture_json, encoding="utf-8") as f:
            row = json.load(f)
        scenario = hf_row_to_scenario(row) if isinstance(row, dict) else scenarios_from_records(row)[0]
    else:
        scenario = load_one_scenario(
            split=args.split,  # type: ignore[arg-type]
            scenario_id=args.scenario_id,
            index=args.index,
        )

    state, trace, result = run_scenario_partial(
        scenario,
        include_execution=bool(args.include_execution),
    )
    scorecard = build_scorecard([result])

    print("=== Scenario ===")
    print(f"id:          {scenario.scenario_id}")
    print(f"title:       {scenario.title}")
    print(f"root_cause:  {scenario.root_cause[:120]}...")
    print(f"resolution:  {scenario.resolution_steps[0][:100] if scenario.resolution_steps else 'n/a'}...")
    print()
    print("=== Agent (partial) ===")
    print(f"diagnosis:   {state.diagnosis.category if state.diagnosis else None}")
    print(f"hypothesis:  {trace.confirmed_hypothesis}")
    print(f"action:      {trace.selected_action}")
    print(f"confidence:  {trace.predicted_confidence:.3f}")
    print(f"phase:       {state.phase}")
    print()
    print("=== Ground-truth compare ===")
    print(f"diagnosis_matches_root_cause:     {trace.metadata.get('diagnosis_matches_root_cause')}")
    print(f"plan_matches_resolution_steps:    {trace.metadata.get('plan_matches_resolution_steps')}")
    print(f"resolved (eval):                  {trace.resolved}")
    print()
    print("=== Scorecard (1 scenario) ===")
    print(f"diagnosis_accuracy:       {result.scores.diagnosis_accuracy:.3f}")
    print(f"investigation_efficiency: {result.scores.investigation_efficiency:.3f}")
    print(f"confidence_calibration:   {result.scores.confidence_calibration:.3f}")
    print(f"remediation_safety:       {result.scores.remediation_safety:.3f}")
    print(f"recovery_time:            {result.scores.recovery_time:.3f}")
    print(f"overall:                  {result.scores.overall:.3f}")
    print(f"mean_overall (card):      {scorecard.mean_overall:.3f}")

    if args.out:
        payload = {
            "scenario_id": scenario.scenario_id,
            "trace": trace.model_dump(mode="json"),
            "scores": result.scores.model_dump(mode="json"),
            "scorecard": scorecard.model_dump(mode="json"),
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"\nWrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
