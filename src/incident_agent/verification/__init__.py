"""Hypothesis verification: challenge priors against observed evidence."""

from incident_agent.verification.bayesian import (
    LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH,
    LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH_SPEC,
    bayesian_update,
    status_from_posterior,
    update_for_continuous_memory_growth,
)
from incident_agent.verification.engine import verify_hypotheses_from_state
from incident_agent.verification.evaluator import (
    detect_monotonically_increasing,
    detect_step_increase_or_spike,
)
from incident_agent.verification.loop import (
    BayesianVerificationResult,
    initial_oom_hypotheses,
    plan_oom_metric_evidence,
    run_bayesian_verification_loop,
)

__all__ = [
    "BayesianVerificationResult",
    "LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH",
    "LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH_SPEC",
    "bayesian_update",
    "detect_monotonically_increasing",
    "detect_step_increase_or_spike",
    "initial_oom_hypotheses",
    "plan_oom_metric_evidence",
    "run_bayesian_verification_loop",
    "status_from_posterior",
    "update_for_continuous_memory_growth",
    "verify_hypotheses_from_state",
]
