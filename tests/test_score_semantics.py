"""Belief vs suitability vs gate score — see docs/SCORE_SEMANTICS.md."""

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    ConfidenceScore,
    Diagnosis,
    Hypothesis,
    IncidentState,
    Observations,
    RemediationOption,
)
from incident_agent.remediation.decision import decide_remediation
from incident_agent.remediation.options import templates_for


def test_hypothesis_belief_aliases_likelihood() -> None:
    hyp = Hypothesis(
        hypothesis_id="h1",
        description="Memory leak",
        likelihood=0.70,
    )
    assert hyp.belief == hyp.likelihood == 0.70


def test_remediation_suitability_aliases_confidence() -> None:
    opt = RemediationOption(
        option_id="r1-increase_memory_limit",
        action="increase_memory_limit",
        expected_effect="Prevent OOM",
        confidence=0.595,
        rationale="test",
    )
    assert opt.suitability == opt.confidence == 0.595


def test_option_suitability_is_catalog_weight_times_belief() -> None:
    templates = templates_for(cause="Memory leak", remediation_key=None)
    increase = next(t for t in templates if t.action == "increase_memory_limit")
    assert increase.base_effectiveness == increase.base_confidence == 0.85

    state = IncidentState(
        incident_id="inc-semantics-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["OOMKilled"],
            extra={"target_ref": "deployment/demo"},
        ),
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9),
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1-memory_leak",
                description="Memory leak",
                likelihood=0.70,
                remediation_key="Resource Constraint (OOMKilled)",
            )
        ],
    )
    decision = decide_remediation(state)
    assert decision.chosen is not None
    assert decision.chosen.action == "increase_memory_limit"
    # 0.85 catalog suitability × 0.70 belief. Not claimed as P(success).
    assert decision.chosen.suitability == 0.595
    assert decision.chosen.confidence == decision.chosen.suitability


def test_gate_score_is_a_separate_contract() -> None:
    score = ConfidenceScore(score=0.42, explanation="top_belief=0.420 * validation_score=1.0")
    assert score.score == 0.42
    assert "belief" in score.explanation
