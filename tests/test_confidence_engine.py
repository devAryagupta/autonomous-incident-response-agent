"""Gate score must not re-apply the verification verdict already in the posterior."""

from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    Diagnosis,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    Observations,
    ValidationVerdict,
)
from incident_agent.nodes.confidence_engine import compute_confidence


def _state(
    *,
    belief: float,
    verification: str | None,
    validation_passed: bool = True,
) -> IncidentState:
    hyp = Hypothesis(
        hypothesis_id="h1-memory_leak",
        description="Memory leak",
        likelihood=belief,
    )
    verifications: list[HypothesisVerification] = []
    if verification is not None:
        verifications.append(
            HypothesisVerification(
                hypothesis_id=hyp.hypothesis_id,
                hypothesis=hyp.description,
                result=verification,  # type: ignore[arg-type]
            )
        )
    return IncidentState(
        incident_id="inc-gate-1",
        created_at=datetime.now(tz=UTC),
        alert=Alert(
            alert_name="CrashLoopBackOff",
            severity="critical",
            starts_at=datetime.now(tz=UTC),
        ),
        observations=Observations(logs=["OOMKilled"]),
        diagnosis=Diagnosis(summary="OOMKilled", category="OOMKilled", confidence=0.9),
        hypotheses=[hyp],
        hypothesis_verifications=verifications,
        validation_verdict=ValidationVerdict(
            passed=validation_passed,
            reason="patch_resource supported" if validation_passed else "unsupported",
        ),
    )


def test_gate_score_is_posterior_times_validation() -> None:
    updates = compute_confidence(_state(belief=0.60, verification="confirmed"))
    assert updates["confidence_score"] == 0.6
    assert "top_belief=0.600" in updates["confidence"].explanation
    assert "verification_factor" not in updates["confidence"].explanation


def test_same_posterior_same_gate_regardless_of_verdict() -> None:
    """The verdict already chose the Bayes factor; it must not change the gate again."""
    confirmed = compute_confidence(_state(belief=0.60, verification="confirmed"))
    inconclusive = compute_confidence(_state(belief=0.60, verification="inconclusive"))
    contradicted = compute_confidence(_state(belief=0.60, verification="contradicted"))
    assert confirmed["confidence_score"] == 0.6
    assert inconclusive["confidence_score"] == 0.6
    assert contradicted["confidence_score"] == 0.6


def test_failed_validation_zeros_the_gate() -> None:
    updates = compute_confidence(
        _state(belief=0.90, verification="confirmed", validation_passed=False)
    )
    assert updates["confidence_score"] == 0.0
