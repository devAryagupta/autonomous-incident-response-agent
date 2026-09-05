from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    Evidence,
    FixAction,
    FixActionType,
    FixPlan,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    Observations,
    RemediationOption,
    RiskLevel,
    ValidationVerdict,
)
from incident_agent.execution import build_execution_plan, check_preconditions
from incident_agent.nodes.approve import approve
from incident_agent.nodes.execute_fix import execute_fix
from incident_agent.nodes.prepare_execution import prepare_execution
from incident_agent.nodes.pre_execute_validate import pre_execute_validate
from incident_agent.nodes.verify_outcome import verify_outcome
from incident_agent.providers import DryRunExecutionProvider, default_providers


def _state_ready_for_execution() -> IncidentState:
    return IncidentState(
        incident_id="inc-exec-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(
            logs=["OOMKilled", "exit status 137"],
            events=["Back-off restarting failed container"],
            extra={"target_ref": "deployment/payment-service", "top_n": 3},
        ),
        diagnosis=Diagnosis(
            summary="OOMKilled",
            category="OOMKilled",
            confidence=0.9,
            evidence=[Evidence(source="events", text="OOMKilled event detected")],
        ),
        hypotheses=[
            Hypothesis(
                hypothesis_id="h1-memory_leak",
                description="Memory leak",
                likelihood=0.75,
                remediation_key="Resource Constraint (OOMKilled)",
            )
        ],
        hypothesis_verifications=[
            HypothesisVerification(
                hypothesis_id="h1-memory_leak",
                hypothesis="Memory leak",
                result="confirmed",
                expected_evidence=["Memory grows continuously"],
                observed_evidence=["Memory increased from 200Mi to 900Mi"],
            )
        ],
        remediation_options=[
            RemediationOption(
                option_id="r1-increase_memory_limit",
                action="increase_memory_limit",
                expected_effect="Prevent OOM",
                risk=RiskLevel.MEDIUM,
                blast_radius="low",
                reversibility="high",
                rollback_possible=True,
                confidence=0.85,
                safety_score=0.8,
                hypothesis_id="h1-memory_leak",
            )
        ],
        chosen_remediation_id="r1-increase_memory_limit",
        chosen_hypothesis_id="h1-memory_leak",
        fix_plan=FixPlan(
            hypothesis_id="h1-memory_leak",
            remediation_option_id="r1-increase_memory_limit",
            risk=RiskLevel.MEDIUM,
            actions=[
                FixAction(
                    action_type=FixActionType.PATCH_RESOURCE,
                    target="deployment/payment-service",
                    params={
                        "action": "increase_memory_limit",
                        "change": "Increase memory requests/limits for the workload",
                    },
                    rationale="Prevent OOM",
                )
            ],
        ),
        validation_verdict=ValidationVerdict(passed=True, reason="structural ok"),
        confidence_score=0.8,
    )


def test_build_execution_plan_shape() -> None:
    state = _state_ready_for_execution()
    plan = build_execution_plan(state)
    assert plan.action == "increase_memory_limit"
    assert plan.target == "deployment/payment-service"
    assert "deployment exists" in plan.preconditions
    assert "rollback available" in plan.preconditions
    assert "restart count decreases" in plan.expected_outcome
    assert "pod becomes healthy" in plan.expected_outcome
    assert plan.rollback_action == "restore_previous_limit"


def test_dry_run_returns_structured_execution_result() -> None:
    state = _state_ready_for_execution()
    state.execution_plan = build_execution_plan(state)
    result = DryRunExecutionProvider().execute(state.fix_plan, state=state)
    assert result.success is True
    assert result.executed is False
    assert result.status == "dry_run_success"
    assert result.action == "increase_memory_limit"
    assert any("memory limit updated" in c.lower() for c in result.applied_changes)
    assert result.details["status"] == "success"


def test_execution_lifecycle_resolves_after_outcome_verification() -> None:
    state = _state_ready_for_execution()
    providers = default_providers()

    state.execution_plan = prepare_execution(state)["execution_plan"]  # type: ignore[assignment]
    assert check_preconditions(state).passed is True

    pre = pre_execute_validate(state)
    state.validation = pre["validation"]  # type: ignore[assignment]
    state.validation_verdict = pre["validation_verdict"]  # type: ignore[assignment]

    state.approval = approve(state)["approval"]  # type: ignore[assignment]
    assert state.approval.approved is True

    exec_updates = execute_fix(state, providers=providers)
    state.execution = exec_updates["execution"]  # type: ignore[assignment]
    assert state.execution.success is True
    assert state.execution.action == "increase_memory_limit"

    outcome_updates = verify_outcome(state, providers=providers)
    state.outcome_verification = outcome_updates["outcome_verification"]  # type: ignore[assignment]
    state.incident_resolved = outcome_updates["incident_resolved"]  # type: ignore[assignment]

    assert state.incident_resolved is True
    assert state.outcome_verification.resolved is True
    assert "restart count decreases" in state.outcome_verification.observed_outcome
    assert "pod becomes healthy" in state.outcome_verification.observed_outcome
    flags = state.outcome_verification.assessment
    assert flags.execution_success is True
    assert flags.service_recovered is True
    assert flags.stable_recovery is True
    # Mitigation of a leak is not root-cause evidence.
    assert flags.root_cause_verified is False


def test_command_success_alone_does_not_resolve_without_outcomes() -> None:
    from incident_agent.contracts import Approval
    from incident_agent.execution.outcome import verify_execution_outcome

    state = _state_ready_for_execution()
    state.execution_plan = build_execution_plan(state)
    # Force empty expected outcomes to prove we never auto-resolve on execute alone.
    state.execution_plan = state.execution_plan.model_copy(update={"expected_outcome": []})
    state.approval = Approval(approved=True, by="test", comment="force")
    state.execution = DryRunExecutionProvider().execute(state.fix_plan, state=state)
    assert state.execution.success is True

    outcome, _obs = verify_execution_outcome(state, providers=default_providers())
    assert outcome.resolved is False
    assert outcome.unmet_expectations
