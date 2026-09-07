"""Four-layer outcome: execute → recover → stay healthy → cause addressed."""

from __future__ import annotations

from collections.abc import Sequence

from incident_agent.contracts import IncidentState, RemediationPurpose, ResolutionAssessment
from incident_agent.remediation.options import purpose_for

_STABILITY_SIGNALS = frozenset(
    {
        "restart count decreases",
        "no recent restarts",
        "crashloop clears",
    }
)


def confirmed_cause(state: IncidentState) -> str:
    for verification in state.hypothesis_verifications:
        if verification.result == "confirmed":
            return verification.hypothesis
    if state.hypotheses:
        chosen = state.chosen_hypothesis_id
        if chosen:
            for hyp in state.hypotheses:
                if hyp.hypothesis_id == chosen:
                    return hyp.description
        return state.hypotheses[0].description
    return ""


def applied_action(state: IncidentState) -> str:
    if state.execution and state.execution.action:
        return state.execution.action
    if state.execution_plan and state.execution_plan.action:
        return state.execution_plan.action
    if state.chosen_remediation_id:
        for option in state.remediation_options:
            if option.option_id == state.chosen_remediation_id:
                return option.action
    return ""


def execution_succeeded(state: IncidentState) -> bool:
    result = state.execution
    if result is None or result.status == "skipped":
        return False
    return bool(result.success)


def service_was_recovered(state: IncidentState) -> bool:
    if state.outcome_verification is not None:
        return bool(state.outcome_verification.resolved)
    return state.incident_resolved is True


def assess_resolution(
    *,
    execution_success: bool,
    service_recovered: bool,
    observed_outcomes: Sequence[str],
    action: str,
    confirmed_hypothesis: str,
) -> ResolutionAssessment:
    """Populate what Stage 0 can observe. Unobserved layers stay false.

    Purpose comes from the remediation catalog for this cause+action:
    temporary recovery never counts as stable or root-cause;
    mitigation can look stable but is not root-cause verified.
    """
    purpose = purpose_for(cause=confirmed_hypothesis, action=action)
    temporary = purpose == RemediationPurpose.TEMPORARY_RECOVERY
    observed = {item.strip().lower() for item in observed_outcomes if item}
    stable = (
        service_recovered
        and not temporary
        and bool(observed & _STABILITY_SIGNALS)
    )
    root_verified = service_recovered and purpose == RemediationPurpose.ROOT_CAUSE
    return ResolutionAssessment(
        execution_success=execution_success,
        service_recovered=service_recovered,
        stable_recovery=stable,
        root_cause_verified=root_verified,
    )


def assess_resolution_from_state(state: IncidentState) -> ResolutionAssessment:
    observed: list[str] = []
    if state.outcome_verification is not None:
        observed = list(state.outcome_verification.observed_outcome)
    return assess_resolution(
        execution_success=execution_succeeded(state),
        service_recovered=service_was_recovered(state),
        observed_outcomes=observed,
        action=applied_action(state),
        confirmed_hypothesis=confirmed_cause(state),
    )
