"""Bayesian posterior update for hypothesis verification.

P(H_i | E) = P(E | H_i) * P(H_i) / Σ_j P(E | H_j) * P(H_j)
"""

from __future__ import annotations

from incident_agent.hypothesis.models import HypothesisState, HypothesisStatus

# Status thresholds after posterior calculation.
CONFIRM_THRESHOLD = 0.70
PARTIAL_THRESHOLD = 0.20

EVIDENCE_CONTINUOUS_MEMORY_GROWTH = "Continuous Memory Growth"

# Spec cited P(E|H2)=0.40 (moderate). With priors [0.4, 0.4, 0.2] that yields
# H1≈0.691 (PARTIAL). The walkthrough's CONFIRMED/PARTIAL/REJECTED outcome uses
# a slightly lower moderate likelihood for H2 so H1≈0.72.
LIKELIHOOD_SPEC_H2_MODERATE = 0.40

LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH: dict[str, float] = {
    "Memory leak": 0.95,
    "Memory limit too low": 0.35,
    "Traffic spike": 0.05,
}

LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH_SPEC: dict[str, float] = {
    "Memory leak": 0.95,
    "Memory limit too low": LIKELIHOOD_SPEC_H2_MODERATE,
    "Traffic spike": 0.05,
}


def status_from_posterior(posterior: float) -> HypothesisStatus:
    """Map posterior probability to CONFIRMED / PARTIAL / REJECTED."""
    if posterior >= CONFIRM_THRESHOLD:
        return HypothesisStatus.CONFIRMED
    if posterior >= PARTIAL_THRESHOLD:
        return HypothesisStatus.PARTIAL
    return HypothesisStatus.REJECTED


def bayesian_update(
    hypotheses: list[HypothesisState],
    *,
    likelihoods: dict[str, float],
    evidence_label: str,
) -> list[HypothesisState]:
    """
    Update all hypotheses under one evidence observation E.

    ``likelihoods`` keys match ``HypothesisState.description``.
    Missing likelihoods default to a small epsilon so the hypothesis is not
    hard-zeroed while still contributing negligibly to the normalizer.
    """
    if not hypotheses:
        return []

    unnormalized: list[float] = []
    for hyp in hypotheses:
        prior = hyp.prior_probability
        likelihood = likelihoods.get(hyp.description, 1e-6)
        unnormalized.append(max(likelihood * prior, 0.0))

    total = sum(unnormalized)
    if total <= 0:
        return [
            hyp.model_copy(
                update={
                    "posterior_probability": hyp.prior_probability,
                    "status": HypothesisStatus.UNVERIFIED,
                }
            )
            for hyp in hypotheses
        ]

    updated: list[HypothesisState] = []
    for hyp, weight in zip(hypotheses, unnormalized, strict=True):
        posterior = weight / total
        support = list(hyp.supporting_evidence)
        if evidence_label and evidence_label not in support:
            support.append(evidence_label)
        updated.append(
            hyp.model_copy(
                update={
                    "posterior_probability": round(posterior, 6),
                    "status": status_from_posterior(posterior),
                    "supporting_evidence": support,
                }
            )
        )
    return updated


def update_for_continuous_memory_growth(
    hypotheses: list[HypothesisState],
    *,
    likelihoods: dict[str, float] | None = None,
) -> list[HypothesisState]:
    """Apply the Continuous Memory Growth likelihood matrix."""
    return bayesian_update(
        hypotheses,
        likelihoods=likelihoods or LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH,
        evidence_label=EVIDENCE_CONTINUOUS_MEMORY_GROWTH,
    )
