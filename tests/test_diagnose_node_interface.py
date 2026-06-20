def test_diagnose_interface_is_stable() -> None:
    from datetime import UTC, datetime

    from incident_agent.contracts import Alert, IncidentState, Observations
    from incident_agent.nodes.diagnose import diagnose

    state = IncidentState(
        incident_id="inc-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=["anything"], events=[]),
    )
    updates = diagnose(state)
    out = updates["diagnosis"]
    assert out.summary == "Invalid image tag"  # type: ignore[attr-defined]
    assert 0.0 <= out.confidence <= 1.0  # type: ignore[attr-defined]

