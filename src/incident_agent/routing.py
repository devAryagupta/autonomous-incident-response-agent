"""Deterministic confidence routing helpers (plumbing only; no reasoning)."""

from __future__ import annotations

from datetime import UTC
from typing import Literal

from incident_agent.contracts import (
    ExecutionResult,
    FixAction,
    FixActionType,
    FixPlan,
    IncidentState,
    RiskLevel,
)

# Default threshold for "high confidence". Overridable via observations.extra.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

INSUFFICIENT_CONFIDENCE = "INSUFFICIENT_CONFIDENCE"
NOOP_DECISION = "NOOP"

RouteDecision = Literal["execute", "replan", "escalate"]


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

    - High confidence (>= threshold) → execute
    - Low confidence and replan_count < max_replans → replan
    - Low confidence and retries exhausted → escalate (NOOP, no mutation)
    """
    score = get_confidence_score(state)
    threshold = get_confidence_threshold(state)

    if score >= threshold:
        return "execute"
    if state.replan_count < state.max_replans:
        return "replan"
    return "escalate"


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


def _hold_fix_plan(*, hypothesis_id: str | None, target_ref: str, notes: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        remediation_option_id=None,
        risk=RiskLevel.LOW,
        actions=[
            FixAction(
                action_type=FixActionType.NOOP,
                target=target_ref,
                params={"action": "noop", "reason": INSUFFICIENT_CONFIDENCE},
                rationale=(
                    "Insufficient confidence after maximum replans; "
                    "escalate for human investigation."
                ),
            )
        ],
        notes=notes,
    )


def escalate_insufficient_confidence(state: IncidentState) -> dict[str, object]:
    """Hold: do not execute the planned change. Investigation / escalation only."""
    score = get_confidence_score(state)
    threshold = get_confidence_threshold(state)
    target_ref = str(state.observations.extra.get("target_ref", "<workload>"))
    stamp = (
        state.created_at
        if state.created_at.tzinfo
        else state.created_at.replace(tzinfo=UTC)
    )
    notes = (
        f"NOOP {INSUFFICIENT_CONFIDENCE} score={score:.3f} "
        f"threshold={threshold:.3f} replans={state.replan_count}/{state.max_replans}"
    )
    execution = ExecutionResult(
        executed=False,
        success=False,
        status="skipped",
        action="noop",
        applied_changes=[],
        summary="Execution skipped: insufficient confidence after maximum replans",
        details={"decision": NOOP_DECISION, "reason": INSUFFICIENT_CONFIDENCE},
        started_at=stamp,
        finished_at=stamp,
    )
    log = list(state.log)
    log.append(
        f"escalate: decision={NOOP_DECISION} reason={INSUFFICIENT_CONFIDENCE} "
        f"score={score:.3f} threshold={threshold:.3f}"
    )
    return {
        "decision": NOOP_DECISION,
        "decision_reason": INSUFFICIENT_CONFIDENCE,
        "chosen_remediation_id": None,
        "fix_plan": _hold_fix_plan(
            hypothesis_id=state.chosen_hypothesis_id,
            target_ref=target_ref,
            notes=notes,
        ),
        "execution": execution,
        "incident_resolved": False,
        "route": "escalate",
        "phase": "escalate",
        "log": log,
    }


def finalize(
    state: IncidentState,
    *,
    providers=None,
    config: dict | None = None,
) -> dict[str, object]:
    """Terminal node: mark lifecycle done and persist episode to memory."""
    from incident_agent.providers import resolve_providers

    route = state.route if state.route == "escalate" else "end"
    log = list(state.log)
    score = get_confidence_score(state)
    threshold = get_confidence_threshold(state)

    if state.decision == NOOP_DECISION:
        log.append(
            f"finalize: noop reason={state.decision_reason} "
            f"count={state.replan_count}/{state.max_replans} score={score:.3f}"
        )
    elif state.replan_count >= state.max_replans and score < threshold:
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

    bundle = providers or resolve_providers(config)
    memory_id = bundle.memory.store(state)
    if memory_id:
        log.append(f"finalize: memory_stored episode_id={memory_id}")
    else:
        log.append("finalize: memory_stored=<none>")

    return {"phase": "done", "route": route, "log": log}
