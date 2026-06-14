from incident_agent.nodes.diagnose import diagnose


def test_diagnose_interface_is_stable() -> None:
    from datetime import UTC, datetime

    from incident_agent.contracts import Alert

    out = diagnose(
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        logs=["anything"],
    )
    assert out.summary == "Invalid image tag"
    assert 0.0 <= out.confidence <= 1.0

