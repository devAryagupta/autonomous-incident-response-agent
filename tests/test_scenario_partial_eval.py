"""Partial one-scenario eval: IncidentScenario → agent → DecisionTrace → scorecard."""

from __future__ import annotations

from incident_agent.datasets.huggingface import hf_row_to_scenario
from incident_agent.eval import build_scorecard, causes_match, plan_matches_resolution
from incident_agent.eval.run_one import run_scenario_partial

_HF_OOM_ROW = {
    "incident_id": "INC-2024-001",
    "title": "Production Kubernetes Cluster - Pods CrashLooping",
    "severity": "critical",
    "category": "kubernetes",
    "environment": "production",
    "description": "Multiple pods across different namespaces are in CrashLoopBackOff state.",
    "symptoms": [
        "Pods restarting every 30-60 seconds",
        "HTTP 503 errors reported by users",
        "kubectl get pods shows CrashLoopBackOff status",
    ],
    "root_cause": (
        "PostgreSQL pod crashed due to OOMKilled (Out of Memory). "
        "The database pod had a memory limit of 2Gi but actual usage exceeded this."
    ),
    "resolution_steps": [
        "Immediately scaled up memory limit from 2Gi to 4Gi in the StatefulSet",
        "Restarted the PostgreSQL pod to recover service",
    ],
    "tags": ["kubernetes", "oom", "crashloop"],
}


def test_causes_match_narrative_root_cause() -> None:
    gt = _HF_OOM_ROW["root_cause"]
    assert causes_match("OOMKilled", gt)
    assert causes_match("Resource Constraint (OOMKilled)", gt)
    assert not causes_match("Missing Secret", gt)


def test_plan_matches_resolution_keywords() -> None:
    steps = list(_HF_OOM_ROW["resolution_steps"])
    assert plan_matches_resolution("patch_resource", steps, plan_rationale="raise memory limit")
    assert plan_matches_resolution("restart_pod", steps, plan_rationale="restart postgres")
    assert not plan_matches_resolution("noop", steps, plan_rationale="")


def test_run_one_scenario_partial_feeds_scorecard() -> None:
    scenario = hf_row_to_scenario(_HF_OOM_ROW)
    state, trace, result = run_scenario_partial(scenario, include_execution=False)

    assert state.diagnosis is not None
    assert state.fix_plan is not None
    assert state.execution is None  # partial: no execute
    assert trace.scenario_id == "INC-2024-001"
    assert trace.ground_truth_cause == scenario.root_cause
    assert "diagnosis_matches_root_cause" in trace.metadata
    assert "plan_matches_resolution_steps" in trace.metadata

    card = build_scorecard([result])
    assert card.scenario_count == 1
    assert 0.0 <= card.mean_overall <= 1.0
    assert result.scores.overall == card.mean_overall
