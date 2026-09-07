from __future__ import annotations

from incident_agent.contracts import RemediationPurpose
from incident_agent.remediation.options import purpose_for, templates_for


def test_leak_catalog_separates_mitigation_root_cause_and_recovery() -> None:
    by_action = {row.action: row for row in templates_for(cause="Memory leak", remediation_key=None)}
    assert by_action["increase_memory_limit"].purpose == RemediationPurpose.MITIGATION
    assert "not a root-cause fix" in by_action["increase_memory_limit"].expected_effect.lower()
    assert by_action["rollback_deployment"].purpose == RemediationPurpose.ROOT_CAUSE
    assert "root-cause" in by_action["rollback_deployment"].expected_effect.lower()
    assert by_action["restart_pod"].purpose == RemediationPurpose.TEMPORARY_RECOVERY
    assert "temporary recovery" in by_action["restart_pod"].expected_effect.lower()


def test_same_action_changes_purpose_with_cause() -> None:
    assert purpose_for(cause="Memory leak", action="increase_memory_limit") == (
        RemediationPurpose.MITIGATION
    )
    assert purpose_for(cause="Memory limit too low", action="increase_memory_limit") == (
        RemediationPurpose.ROOT_CAUSE
    )
    assert purpose_for(cause="Traffic spike", action="increase_memory_limit") == (
        RemediationPurpose.MITIGATION
    )


def test_restart_is_temporary_recovery_for_every_cataloged_cause() -> None:
    for cause, rows in (
        ("Memory leak", templates_for(cause="Memory leak", remediation_key=None)),
        ("Memory limit too low", templates_for(cause="Memory limit too low", remediation_key=None)),
        ("Secret not created", templates_for(cause="Secret not created", remediation_key=None)),
        (
            "Unhandled exception in application",
            templates_for(cause="Unhandled exception in application", remediation_key=None),
        ),
    ):
        restart = next(row for row in rows if row.action == "restart_pod")
        assert restart.purpose == RemediationPurpose.TEMPORARY_RECOVERY, cause
