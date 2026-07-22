"""Deterministic confidence routing helpers (plumbing only; no reasoning)."""

from __future__ import annotations

from typing import Literal

from incident_agent.contracts import IncidentState

# Default threshold for "high confidence". Overridable via observations.extra.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

RouteDecision = Literal["end", "replan"]


def get_confidence_threshold(state: IncidentState) -> float:
    raw = state.observations.extra.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD)
    return float(raw)


def get_confidence_score(state: IncidentState) -> float:
    if state.confidence_score is not None:
        return float(state.confidence_score)
    if state.confidence is not None:
        return float(state.confidence.score)
    return 0.0


def route_on_confidence(state: IncidentState) -> RouteDecision:
    """
    Conditional router after the confidence node.

    - High confidence (>= threshold) → end (enter execution lifecycle)
    - Low confidence and replan_count < max_replans → replan
    - Low confidence and retries exhausted → end (still enter execution gate;
      prepare/approve may skip unsafe actions)
    """
    score = get_confidence_score(state)
    threshold = get_confidence_threshold(state)

    if score >= threshold:
        return "end"
    if state.replan_count < state.max_replans:
        return "replan"
    return "end"


def bump_replan(state: IncidentState) -> dict[str, object]:
    """Replan node: increment counter, then graph edges back to hypothesize."""
    next_count = int(state.replan_count) + 1
    log = list(state.log)
    log.append(f"replan: count={next_count}/{state.max_replans}")
    return {
        "replan_count": next_count,
        "route": "replan",
        "phase": "replan",
        "log": log,
    }


def finalize(
    state: IncidentState,
    *,
    providers=None,
    config: dict | None = None,
) -> dict[str, object]:
    """Terminal node: mark lifecycle done (execution already ran upstream)."""
    _ = providers, config
    route = "end"
    log = list(state.log)
    score = get_confidence_score(state)
    threshold = get_confidence_threshold(state)

    if state.replan_count >= state.max_replans and score < threshold:
        log.append(
            f"finalize: max_replans_reached count={state.replan_count}/{state.max_replans} "
            f"score={score:.3f}"
        )
    elif score >= threshold:
        log.append(f"finalize: high_confidence score={score:.3f} threshold={threshold:.3f}")

    if state.incident_resolved:
        log.append("finalize: incident_resolved=true")
    elif state.execution is not None and state.execution.success:
        log.append(
            "finalize: execution_succeeded_but_unresolved "
            f"action={state.execution.action}"
        )

    if state.outcome_verification is not None:
        log.append(f"finalize: outcome={state.outcome_verification.reason}")

    return {"phase": "done", "route": route, "log": log}
