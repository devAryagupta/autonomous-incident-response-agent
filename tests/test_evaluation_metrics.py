"""Multi-dimensional evaluation metrics over sample decision traces."""

from __future__ import annotations

import pytest

from incident_agent.eval import (
    DecisionTrace,
    EvidenceCall,
    MultiDimensionalScore,
    ScenarioResult,
    aggregate_brier_score,
    brier_score,
    build_scorecard,
    causes_match,
    diagnosis_accuracy,
    evaluate_scenario,
    investigation_efficiency,
    recovery_time_score,
    remediation_safety,
    score_trace,
)


def _oom_trace(**overrides: object) -> DecisionTrace:
    base = dict(
        scenario_id="sim-oom-001",
        ground_truth_cause="OOMKilled",
        confirmed_hypothesis="Memory leak",
        predicted_diagnosis_category="OOMKilled",
        evidence_calls=[
            EvidenceCall(
                query="container_memory_usage_bytes",
                type="metric",
                high_value=True,
            ),
            EvidenceCall(query="restart_history", type="event", high_value=True),
            EvidenceCall(query="http_requests_per_second", type="metric", high_value=False),
            EvidenceCall(
                query="container_memory_usage_bytes",
                type="metric",
                high_value=True,
                redundant=True,
            ),
        ],
        predicted_confidence=0.92,
        resolved=True,
        selected_action="restart_pod",
        selected_action_risk=1,
        min_adequate_action_risk=1,
        steps=8,
        elapsed_seconds=120.0,
    )
    base.update(overrides)
    return DecisionTrace.model_validate(base)


def test_diagnosis_accuracy_exact_and_alias() -> None:
    assert diagnosis_accuracy("OOMKilled", "OOMKilled") == 1.0
    assert diagnosis_accuracy("Memory leak", "OOMKilled") == 1.0
    assert diagnosis_accuracy("Application Crash", "OOMKilled") == 0.0
    assert diagnosis_accuracy(None, "OOMKilled") == 0.0
    assert causes_match("Resource Constraint (OOMKilled)", "oomkilled")


def test_investigation_efficiency_penalizes_redundant_calls() -> None:
    calls = [
        EvidenceCall(query="container_memory_usage_bytes", high_value=True),
        EvidenceCall(query="restart_history", high_value=True),
        EvidenceCall(query="http_requests_per_second", high_value=False),
        EvidenceCall(
            query="container_memory_usage_bytes",
            high_value=True,
            redundant=True,
        ),
    ]
    # 2 high-value non-redundant / 4 total
    assert investigation_efficiency(calls) == pytest.approx(0.5)

    by_name = investigation_efficiency(
        [
            EvidenceCall(query="container_memory_usage_bytes"),
            EvidenceCall(query="noise_query"),
        ],
        high_value_queries=["container_memory_usage_bytes"],
    )
    assert by_name == pytest.approx(0.5)
    assert investigation_efficiency([]) == 0.0


def test_brier_and_confidence_calibration() -> None:
    # Confident and correct → low Brier, high calibration score
    assert brier_score(0.95, True) == pytest.approx((0.95 - 1.0) ** 2)
    assert brier_score(0.95, True) == pytest.approx(0.0025)
    # Overconfident and wrong → high Brier
    assert brier_score(0.95, False) == pytest.approx((0.95 - 0.0) ** 2)
    assert aggregate_brier_score([(1.0, True), (0.0, False)]) == pytest.approx(0.0)


def test_remediation_safety_parsimony() -> None:
    # Prefer restart_pod (1) over scale_deployment (2) when both adequate.
    assert remediation_safety(1, 1, resolved=True) == 1.0
    assert remediation_safety(2, 1, resolved=True) == pytest.approx(1.0 - 1 / 3)
    assert remediation_safety(4, 1, resolved=True) == 0.0
    assert remediation_safety(1, 1, resolved=False) == 0.0
    # Under-powered selection
    assert remediation_safety(1, 2, resolved=True) == 0.0


def test_recovery_time_score_faster_is_better() -> None:
    fast = recovery_time_score(steps=4, elapsed_seconds=60.0)
    slow = recovery_time_score(steps=20, elapsed_seconds=600.0)
    assert fast > slow
    assert slow == pytest.approx(0.0)


def test_evaluate_scenario_oom_happy_path() -> None:
    result = evaluate_scenario(_oom_trace())
    assert isinstance(result, ScenarioResult)
    assert isinstance(result.scores, MultiDimensionalScore)
    assert result.scores.diagnosis_accuracy == 1.0
    assert result.scores.investigation_efficiency == pytest.approx(0.5)
    assert result.scores.remediation_safety == 1.0
    assert result.scores.brier_score == pytest.approx((0.92 - 1.0) ** 2)
    assert result.scores.confidence_calibration == pytest.approx(
        1.0 - result.scores.brier_score
    )
    assert result.scores.mttr_steps == 8
    assert result.scores.mttr_seconds == 120.0
    assert 0.0 < result.scores.overall <= 1.0


def test_unsafe_overconfident_unresolved_trace_scores_low() -> None:
    trace = _oom_trace(
        confirmed_hypothesis="Traffic spike",
        predicted_diagnosis_category="Application Crash",
        predicted_confidence=0.95,
        resolved=False,
        selected_action="scale_deployment",
        selected_action_risk=2,
        min_adequate_action_risk=1,
        steps=18,
        elapsed_seconds=500.0,
    )
    scores = score_trace(trace)
    assert scores.diagnosis_accuracy == 0.0
    assert scores.remediation_safety == 0.0
    assert scores.brier_score == pytest.approx(0.9025)
    assert scores.overall < 0.4


def test_scorecard_aggregates_multiple_scenarios() -> None:
    good = evaluate_scenario(_oom_trace(scenario_id="a"))
    bad = evaluate_scenario(
        _oom_trace(
            scenario_id="b",
            confirmed_hypothesis="Application Crash",
            predicted_diagnosis_category="Application Crash",
            predicted_confidence=0.9,
            resolved=False,
            selected_action_risk=3,
            steps=15,
            elapsed_seconds=400.0,
        )
    )
    card = build_scorecard([good, bad])
    assert card.scenario_count == 2
    assert card.mean_diagnosis_accuracy == pytest.approx(0.5)
    assert card.aggregate_brier_score == pytest.approx(
        aggregate_brier_score(
            [
                (good.trace.predicted_confidence, good.resolved),
                (bad.trace.predicted_confidence, bad.resolved),
            ]
        )
    )
    assert 0.0 < card.mean_overall < 1.0


def test_prefer_restart_over_scale_in_safety_dimension() -> None:
    restart = evaluate_scenario(
        _oom_trace(
            selected_action="restart_pod",
            selected_action_risk=1,
            min_adequate_action_risk=1,
        )
    )
    scale = evaluate_scenario(
        _oom_trace(
            scenario_id="sim-oom-scale",
            selected_action="scale_deployment",
            selected_action_risk=2,
            min_adequate_action_risk=1,
        )
    )
    assert restart.scores.remediation_safety > scale.scores.remediation_safety
