from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    Evidence,
    FixActionType,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    Observations,
)
from incident_agent.nodes.plan_fix import plan_fix
from incident_agent.remediation.decision import decide_remediation


def _state_with_hyps(hyps: list[Hypothesis], *, verifications: list[HypothesisVerification] | None = None) -> IncidentState:
    state = IncidentState(
        incident_id="inc-remediation-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["OOMKilled"],
            events=["Back-off restarting failed container"],
            extra={"target_ref": "deployment/payment-service", "top_n": 3},
        ),
        diagnosis=Diagnosis(
            summary="OOMKilled",
            category="OOMKilled",
            confidence=0.9,
            evidence=[Evidence(source="events", text="OOMKilled event detected")],
        ),
        hypotheses=hyps,
        hypothesis_verifications=verifications or [],
        chosen_hypothesis_id=hyps[0].hypothesis_id if hyps else None,
    )
    return state


def test_oom_chooses_increase_memory_over_rollback() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-memory_leak",
            description="Memory leak",
            likelihood=0.70,
            remediation_key="Resource Constraint (OOMKilled)",
            verification_checks=["Check heap usage over time"],
        ),
        Hypothesis(
            hypothesis_id="h2-memory_limit_too_low",
            description="Memory limit too low",
            likelihood=0.20,
            remediation_key="Resource Constraint (OOMKilled)",
        ),
        Hypothesis(
            hypothesis_id="h3-traffic_spike",
            description="Traffic spike",
            likelihood=0.10,
            remediation_key="Resource Constraint (OOMKilled)",
        ),
    ]
    verifications = [
        HypothesisVerification(
            hypothesis_id="h1-memory_leak",
            hypothesis="Memory leak",
            expected_evidence=["Memory grows continuously"],
            observed_evidence=["Memory increased from 200Mi to 900Mi"],
            result="confirmed",
            confidence_delta=0.2,
        )
    ]
    decision = decide_remediation(_state_with_hyps(hyps, verifications=verifications))
    assert decision.chosen is not None
    assert decision.chosen.action == "increase_memory_limit"
    assert decision.chosen.purpose.value == "mitigation"
    assert decision.chosen.blast_radius == "low"
    assert decision.chosen.reversibility == "high"
    assert decision.chosen.rollback_possible is True
    assert decision.chosen.expected_effect
    assert decision.options[0].action == "increase_memory_limit"
    # Rollback exists as a candidate but loses on blast radius / safety.
    assert any(o.action == "rollback_deployment" for o in decision.options)
    assert decision.fix_plan.actions[0].action_type == FixActionType.PATCH_RESOURCE
    assert decision.fix_plan.remediation_option_id == decision.chosen.option_id


def test_secret_prefers_config_fix_with_low_blast_radius() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-secret_not_created",
            description="Secret not created",
            likelihood=0.80,
            remediation_key="Missing Secret",
        ),
        Hypothesis(
            hypothesis_id="h2-wrong_secret_ref",
            description="Wrong secret name or namespace",
            likelihood=0.15,
            remediation_key="Missing Secret",
        ),
    ]
    decision = decide_remediation(_state_with_hyps(hyps))
    assert decision.chosen is not None
    assert decision.chosen.action == "create_or_fix_secret"
    assert decision.chosen.risk.value == "low"
    assert decision.chosen.blast_radius == "low"
    assert decision.fix_plan.risk.value == "low"


def test_plan_fix_node_writes_options_and_fix_plan() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-memory_limit_too_low",
            description="Memory limit too low",
            likelihood=0.85,
            remediation_key="Resource Constraint (OOMKilled)",
        )
    ]
    state = _state_with_hyps(hyps)
    updates = plan_fix(state)
    assert updates["chosen_remediation_id"]
    assert updates["remediation_options"]
    assert updates["fix_plan"].actions
    top = updates["remediation_options"][0]
    assert top.action == "increase_memory_limit"
    assert 0.0 <= top.confidence <= 1.0
    assert 0.0 <= top.safety_score <= 1.0


def test_low_effectiveness_restart_does_not_beat_safer_fix() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-unhandled_exception",
            description="Unhandled exception in application",
            likelihood=0.75,
            remediation_key="Application Bug / Unhandled Exception",
        )
    ]
    verifications = [
        HypothesisVerification(
            hypothesis_id="h1-unhandled_exception",
            hypothesis="Unhandled exception in application",
            result="confirmed",
            expected_evidence=["Stack trace"],
            observed_evidence=["Traceback (most recent call last):"],
            confidence_delta=0.15,
        )
    ]
    decision = decide_remediation(_state_with_hyps(hyps, verifications=verifications))
    assert decision.chosen is not None
    # Restart is lower blast radius but much lower suitability / expected effect.
    assert decision.chosen.action == "rollback_deployment"
    assert decision.fix_plan.actions[0].action_type == FixActionType.ROLLBACK_DEPLOYMENT


def test_minimum_effective_action_prefers_safer_when_effective_set_is_close() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-traffic_spike",
            description="Traffic spike",
            likelihood=0.70,
            remediation_key="Resource Constraint (OOMKilled)",
        ),
        Hypothesis(
            hypothesis_id="h2-memory_limit_too_low",
            description="Memory limit too low",
            likelihood=0.65,
            remediation_key="Resource Constraint (OOMKilled)",
        ),
    ]
    verifications = [
        HypothesisVerification(
            hypothesis_id="h1-traffic_spike",
            hypothesis="Traffic spike",
            result="confirmed",
            confidence_delta=0.12,
        ),
        HypothesisVerification(
            hypothesis_id="h2-memory_limit_too_low",
            hypothesis="Memory limit too low",
            result="confirmed",
            confidence_delta=0.10,
        ),
    ]
    decision = decide_remediation(_state_with_hyps(hyps, verifications=verifications))
    assert decision.chosen is not None
    assert decision.chosen.action == "increase_memory_limit"
    assert decision.chosen.blast_radius == "low"
    assert decision.chosen.confidence >= 0.4


def test_blocks_when_no_option_meets_effectiveness_floor() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-fatal_runtime_error",
            description="Fatal runtime error",
            likelihood=0.4275,
            remediation_key="Application Bug / Unhandled Exception",
        )
    ]
    verifications = [
        HypothesisVerification(
            hypothesis_id="h1-fatal_runtime_error",
            hypothesis="Fatal runtime error",
            result="confirmed",
            expected_evidence=["Panic/fatal runtime signal appears in logs"],
            observed_evidence=["panic: startup initialization failed in bootstrap()"],
            confidence_delta=0.3925,
        )
    ]

    decision = decide_remediation(_state_with_hyps(hyps, verifications=verifications))

    # rollback_deployment=0.342 and restart_pod=0.0855, both below 0.40 floor.
    assert decision.chosen is None
    assert decision.matched_rule == "remediation_decision.v1:blocked_ineffective"
    assert [o.action for o in decision.options] == ["rollback_deployment", "restart_pod"]
    assert decision.fix_plan.actions[0].action_type == FixActionType.NOOP
    assert decision.fix_plan.actions[0].params["action"] == "noop_investigate"
    assert decision.fix_plan.notes is not None
    assert "effectiveness floor" in decision.fix_plan.notes


def test_option_confidence_ignores_verification_verdict() -> None:
    """Posterior belief already encodes verification; verdict must not apply again."""
    hyps = [
        Hypothesis(
            hypothesis_id="h1-memory_leak",
            description="Memory leak",
            likelihood=0.70,
            remediation_key="Resource Constraint (OOMKilled)",
        )
    ]
    contradicted = [
        HypothesisVerification(
            hypothesis_id="h1-memory_leak",
            hypothesis="Memory leak",
            result="contradicted",
            confidence_delta=-0.3,
        )
    ]
    with_verdict = decide_remediation(_state_with_hyps(hyps, verifications=contradicted))
    without_verdict = decide_remediation(_state_with_hyps(hyps))
    assert with_verdict.chosen is not None
    assert without_verdict.chosen is not None
    assert with_verdict.chosen.action == without_verdict.chosen.action
    assert with_verdict.chosen.confidence == without_verdict.chosen.confidence
    # increase_memory_limit catalog suitability 0.85 × belief 0.70
    assert with_verdict.chosen.suitability == 0.595


def test_increase_memory_limit_plan_keeps_explicit_target_quantity() -> None:
    hyps = [
        Hypothesis(
            hypothesis_id="h1-memory_limit_too_low",
            description="Memory limit too low",
            likelihood=0.90,
            remediation_key="Resource Constraint (OOMKilled)",
        )
    ]
    state = _state_with_hyps(hyps)
    state.observations.extra["new_memory_limit"] = "2Gi"
    decision = decide_remediation(state)
    assert decision.chosen is not None
    assert decision.chosen.action == "increase_memory_limit"
    params = decision.fix_plan.actions[0].params
    assert params.get("new_memory_limit") == "2Gi"
    assert params.get("memory_limit") == "2Gi"
    assert "512Mi" not in str(params)
