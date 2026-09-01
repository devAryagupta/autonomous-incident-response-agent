from datetime import UTC, datetime

from incident_agent.contracts import Alert, IncidentState, Observations
from incident_agent.nodes.diagnose import diagnose


def _state(*, logs: list[str] | None = None, events: list[str] | None = None) -> IncidentState:
    return IncidentState(
        incident_id="inc-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=logs or [], events=events or []),
    )


def test_diagnose_interface_returns_diagnosis() -> None:
    updates = diagnose(_state(logs=["anything"], events=[]))
    out = updates["diagnosis"]
    assert out.category == "Application Failure"
    assert out.summary
    assert 0.0 <= out.confidence <= 1.0
    assert isinstance(out.evidence, list)


def test_diagnose_oomkilled_from_exit_code_and_event() -> None:
    updates = diagnose(
        _state(
            events=["Warning  OOMKilled  kubelet  Container killed due to OOM"],
            logs=["OOMKilled: Container was killed due to memory usage", "exit status 137"],
        )
    )
    out = updates["diagnosis"]
    assert out.category == "OOMKilled"
    assert out.confidence >= 0.85
    texts = [e.text for e in out.evidence]
    assert any("137" in t for t in texts)
    assert any("OOMKilled" in t for t in texts)


def test_diagnose_invalid_configuration_from_failedmount() -> None:
    updates = diagnose(
        _state(
            events=[
                'Warning  FailedMount kubelet  MountVolume.SetUp failed for volume '
                '"secret": secret "db-credentials" not found',
            ],
            logs=["configuration missing: secret db-credentials"],
        )
    )
    out = updates["diagnosis"]
    assert out.category == "Invalid Configuration"
    assert out.confidence >= 0.85
    assert out.evidence


def test_diagnose_invalid_configuration_from_missing_secret() -> None:
    updates = diagnose(
        _state(
            events=[
                'Warning  FailedMount kubelet  MountVolume.SetUp failed for volume '
                '"secret": secret "db-credentials" not found',
            ],
            logs=["FATAL: could not load credentials from /etc/secrets/creds.json"],
        )
    )
    out = updates["diagnosis"]
    assert out.category == "Invalid Configuration"
    assert out.confidence >= 0.8
    assert any(
        "secret" in e.text.lower() or "failedmount" in e.text.lower()
        for e in out.evidence
    )


def test_diagnose_application_failure_from_traceback() -> None:
    updates = diagnose(
        _state(
            events=["Warning  BackOff  kubelet  Back-off restarting failed container"],
            logs=[
                "Traceback (most recent call last):",
                "Exception: Unhandled exception at startup",
                "exit status 1",
            ],
        )
    )
    out = updates["diagnosis"]
    assert out.category == "Application Failure"
    assert out.confidence >= 0.7
    assert out.evidence


def test_diagnose_exit_code_one_is_low_weight() -> None:
    updates = diagnose(
        _state(
            events=["Warning  BackOff  kubelet  Back-off restarting failed container"],
            logs=["process exited with code 1"],
        )
    )
    out = updates["diagnosis"]
    assert out.category == "Application Failure"
    assert out.confidence <= 0.4
    assert any("low diagnostic weight" in e.text.lower() for e in out.evidence)


def test_diagnose_is_deterministic() -> None:
    state = _state(
        events=["Warning  OOMKilled  kubelet  Container killed due to OOM"],
        logs=["exit code 137"],
    )
    a = diagnose(state)["diagnosis"]
    b = diagnose(state)["diagnosis"]
    assert a.category == b.category == "OOMKilled"
    assert a.confidence == b.confidence
    assert [e.text for e in a.evidence] == [e.text for e in b.evidence]
