"""Build ``DecisionTrace`` from agent ``IncidentState`` + scenario ground truth."""

from __future__ import annotations

from datetime import UTC, datetime

from incident_agent.contracts import IncidentState, RiskLevel
from incident_agent.datasets.scenario import IncidentScenario
from incident_agent.eval.metrics import causes_match
from incident_agent.eval.models import DecisionTrace, EvidenceCall

_RISK_TO_INT: dict[RiskLevel, int] = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
}

# Shared vocabulary between agent action names and HF resolution prose.
_RESOLUTION_KEYWORDS: tuple[str, ...] = (
    "memory",
    "oom",
    "limit",
    "restart",
    "scale",
    "secret",
    "rollback",
    "image",
    "dns",
    "certificate",
    "redis",
    "disk",
    "prune",
    "unlock",
    "patch",
)


def _confirmed_hypothesis_text(state: IncidentState) -> str | None:
    if state.chosen_hypothesis_id:
        for hyp in state.hypotheses:
            if hyp.hypothesis_id == state.chosen_hypothesis_id:
                return hyp.description
    if state.diagnosis is not None:
        return state.diagnosis.summary
    return None


def _selected_action(state: IncidentState) -> str | None:
    if state.chosen_remediation_id and state.remediation_options:
        for opt in state.remediation_options:
            if opt.option_id == state.chosen_remediation_id:
                return opt.action
    if state.fix_plan and state.fix_plan.actions:
        first = state.fix_plan.actions[0]
        return first.action_type.value
    return None


def _selected_action_risk(state: IncidentState) -> int:
    if state.chosen_remediation_id and state.remediation_options:
        for opt in state.remediation_options:
            if opt.option_id == state.chosen_remediation_id:
                return _RISK_TO_INT.get(opt.risk, 2)
    if state.fix_plan is not None:
        return _RISK_TO_INT.get(state.fix_plan.risk, 2)
    return 2


def _evidence_calls(state: IncidentState) -> list[EvidenceCall]:
    calls: list[EvidenceCall] = []
    seen: set[str] = set()
    for req in state.evidence_requests:
        key = f"{req.type}:{req.query}"
        redundant = key in seen
        seen.add(key)
        calls.append(
            EvidenceCall(
                query=req.query,
                type=req.type,
                high_value=bool(req.hypothesis_id),
                redundant=redundant,
            )
        )
    if calls:
        return calls
    # Fallback: treat fulfilled results as the investigation trail.
    for result in state.evidence_results:
        key = f"{result.type}:{result.query}"
        redundant = key in seen
        seen.add(key)
        calls.append(
            EvidenceCall(
                query=result.query,
                type=result.type,
                high_value=True,
                redundant=redundant,
            )
        )
    return calls


def plan_matches_resolution(
    selected_action: str | None,
    resolution_steps: list[str],
    *,
    plan_rationale: str = "",
) -> bool:
    """True when agent plan and scenario resolution share remediation intent."""
    if not resolution_steps:
        return False
    blob = " ".join(resolution_steps).lower()
    agent = f"{selected_action or ''} {plan_rationale}".lower()
    if not agent.strip():
        return False
    for keyword in _RESOLUTION_KEYWORDS:
        if keyword in blob and keyword in agent:
            return True
    # Direct substring: action token appears in a resolution step.
    action = (selected_action or "").strip().lower().replace("_", " ")
    if action and action != "noop" and any(action in step.lower() for step in resolution_steps):
        return True
    return False


def decision_trace_from_state(
    state: IncidentState,
    scenario: IncidentScenario,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    include_execution: bool = False,
) -> DecisionTrace:
    """
    Map one agent run onto a ``DecisionTrace`` for the scorecard.

    Ground truth comes from the scenario (``root_cause``, ``resolution_steps``).
    Agent fields come from ``IncidentState``.
    """
    started = started_at or state.created_at
    finished = finished_at or datetime.now(tz=UTC)
    elapsed = max(0.0, (finished - started).total_seconds())

    confirmed = _confirmed_hypothesis_text(state)
    diagnosis_category = state.diagnosis.category if state.diagnosis else None
    selected = _selected_action(state)
    rationale = ""
    if state.fix_plan and state.fix_plan.actions:
        rationale = state.fix_plan.actions[0].rationale
    elif state.fix_plan and state.fix_plan.notes:
        rationale = state.fix_plan.notes or ""

    diagnosis_ok = causes_match(confirmed, scenario.root_cause) or causes_match(
        diagnosis_category, scenario.root_cause
    )
    remediation_ok = plan_matches_resolution(
        selected,
        scenario.resolution_steps,
        plan_rationale=rationale,
    )

    if include_execution:
        resolved = bool(state.incident_resolved) and diagnosis_ok
    else:
        # Partial run: "resolved" means diagnosis + plan align with scenario GT.
        resolved = diagnosis_ok and remediation_ok

    confidence = 0.0
    if state.confidence is not None:
        confidence = float(state.confidence.score)
    elif state.confidence_score is not None:
        confidence = float(state.confidence_score)

    # Phases touched ≈ replan passes + core nodes (approx MTTR steps).
    steps = 4 + max(0, state.replan_count) * 3
    if include_execution:
        steps += 4
    if state.log:
        steps = max(steps, len(state.log))

    return DecisionTrace(
        scenario_id=scenario.scenario_id,
        ground_truth_cause=scenario.root_cause,
        confirmed_hypothesis=confirmed,
        predicted_diagnosis_category=diagnosis_category,
        evidence_calls=_evidence_calls(state),
        predicted_confidence=confidence,
        resolved=resolved,
        selected_action=selected,
        selected_action_risk=_selected_action_risk(state),
        min_adequate_action_risk=1,
        steps=steps,
        elapsed_seconds=elapsed,
        started_at=started,
        resolved_at=finished if resolved else None,
        metadata={
            "title": scenario.title,
            "category": scenario.category,
            "resolution_steps": list(scenario.resolution_steps),
            "diagnosis_matches_root_cause": diagnosis_ok,
            "plan_matches_resolution_steps": remediation_ok,
            "include_execution": include_execution,
            "phase": state.phase,
        },
    )
