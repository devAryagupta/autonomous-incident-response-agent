from __future__ import annotations

from incident_agent.memory.resolution import assess_resolution
from incident_agent.memory.models import EpisodeOutcome, outcome_from_assessment


def test_restart_on_leak_is_temporary_recovery() -> None:
    flags = assess_resolution(
        execution_success=True,
        service_recovered=True,
        observed_outcomes=["pod becomes healthy"],
        action="restart_pod",
        confirmed_hypothesis="Memory leak",
    )
    assert flags.execution_success is True
    assert flags.service_recovered is True
    assert flags.stable_recovery is False
    assert flags.root_cause_verified is False
    assert outcome_from_assessment(flags) == EpisodeOutcome.PARTIAL


def test_raising_limit_verifies_undersized_limit_not_a_leak() -> None:
    limit_fix = assess_resolution(
        execution_success=True,
        service_recovered=True,
        observed_outcomes=["restart count decreases", "pod becomes healthy"],
        action="increase_memory_limit",
        confirmed_hypothesis="Memory limit too low",
    )
    assert limit_fix.stable_recovery is True
    assert limit_fix.root_cause_verified is True
    assert outcome_from_assessment(limit_fix) == EpisodeOutcome.SUCCESS

    leak_mitigation = assess_resolution(
        execution_success=True,
        service_recovered=True,
        observed_outcomes=["restart count decreases", "pod becomes healthy"],
        action="increase_memory_limit",
        confirmed_hypothesis="Memory leak",
    )
    assert leak_mitigation.service_recovered is True
    assert leak_mitigation.stable_recovery is True
    assert leak_mitigation.root_cause_verified is False
    assert outcome_from_assessment(leak_mitigation) == EpisodeOutcome.PARTIAL

    leak_rollback = assess_resolution(
        execution_success=True,
        service_recovered=True,
        observed_outcomes=["pod becomes healthy", "crashloop clears"],
        action="rollback_deployment",
        confirmed_hypothesis="Memory leak",
    )
    assert leak_rollback.root_cause_verified is True
    assert leak_rollback.stable_recovery is True


def test_stage0_without_stability_signal_stays_unverified_stable() -> None:
    flags = assess_resolution(
        execution_success=True,
        service_recovered=True,
        observed_outcomes=["pod becomes healthy"],
        action="create_or_fix_secret",
        confirmed_hypothesis="Secret not created",
    )
    assert flags.stable_recovery is False
    assert flags.root_cause_verified is True
