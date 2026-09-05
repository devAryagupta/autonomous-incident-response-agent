"""Remediation Decision Engine: choose minimum effective safe action."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import (
    FixAction,
    FixActionType,
    FixPlan,
    Hypothesis,
    IncidentState,
    RemediationOption,
    RiskLevel,
)
from incident_agent.remediation.options import RemediationTemplate, templates_for

_LEVEL = {"low": 0.0, "medium": 0.5, "high": 1.0}

# Prefer minimum blast radius, then undoability, then heuristic suitability.
_W_BLAST = 0.30
_W_REVERSIBILITY = 0.25
_W_ROLLBACK = 0.15
_W_RISK = 0.15
_W_SUITABILITY = 0.15

# Minimum suitability needed for an option to be considered effective.
_MIN_VIABLE_SUITABILITY = 0.40
# Effective-set floor: keep actions within this margin of the top suitability,
# then choose the safest among them.
_EFFECTIVENESS_MARGIN = 0.10
_BLOCKED_INEFFECTIVE_RULE = "remediation_decision.v1:blocked_ineffective"


@dataclass(frozen=True, slots=True)
class RemediationDecision:
    options: list[RemediationOption]
    chosen: RemediationOption | None
    fix_plan: FixPlan
    matched_rule: str | None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _safety_score(
    *,
    blast_radius: str,
    reversibility: str,
    rollback_possible: bool,
    risk: RiskLevel,
    suitability: float,
) -> float:
    blast = _LEVEL.get(blast_radius, 0.5)
    rev = _LEVEL.get(reversibility, 0.5)
    risk_v = _LEVEL.get(risk.value, 0.5)
    score = (
        (1.0 - blast) * _W_BLAST
        + rev * _W_REVERSIBILITY
        + (1.0 if rollback_possible else 0.0) * _W_ROLLBACK
        + (1.0 - risk_v) * _W_RISK
        + suitability * _W_SUITABILITY
    )
    return round(_clamp(score), 4)


def _option_from_template(
    *,
    template: RemediationTemplate,
    hyp: Hypothesis,
    rank: int,
) -> RemediationOption:
    # baseline effectiveness × current hypothesis belief (posterior).
    # Verification already moved the posterior; do not apply the verdict again.
    suitability = round(_clamp(template.base_effectiveness * float(hyp.belief)), 4)
    safety = _safety_score(
        blast_radius=template.blast_radius,
        reversibility=template.reversibility,
        rollback_possible=template.rollback_possible,
        risk=template.risk,
        suitability=suitability,
    )
    return RemediationOption(
        option_id=f"r{rank}-{template.action}",
        action=template.action,
        expected_effect=template.expected_effect,
        risk=template.risk,
        blast_radius=template.blast_radius,  # type: ignore[arg-type]
        reversibility=template.reversibility,  # type: ignore[arg-type]
        rollback_possible=template.rollback_possible,
        confidence=suitability,
        safety_score=safety,
        hypothesis_id=hyp.hypothesis_id,
        rationale=template.rationale,
        purpose=template.purpose,
    )


def _explicit_memory_limit(state: IncidentState) -> str | None:
    extra = state.observations.extra
    for key in ("new_memory_limit", "memory_limit"):
        raw = extra.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _plan_from_template(
    *,
    template: RemediationTemplate,
    option: RemediationOption,
    target_ref: str,
    state: IncidentState,
) -> FixPlan:
    params: dict = {"change": template.change, "action": template.action}
    if template.commands:
        params["commands"] = list(template.commands)
    if template.action == "increase_memory_limit":
        limit = _explicit_memory_limit(state)
        if limit:
            params["new_memory_limit"] = limit
            params["memory_limit"] = limit
    return FixPlan(
        hypothesis_id=option.hypothesis_id,
        remediation_option_id=option.option_id,
        risk=option.risk,
        actions=[
            FixAction(
                action_type=template.action_type,
                target=target_ref,
                params=params,
                rationale=template.rationale or option.expected_effect,
            )
        ],
        notes=(
            f"Chosen remediation={option.action} purpose={option.purpose.value} "
            f"blast_radius={option.blast_radius} "
            f"reversibility={option.reversibility} rollback_possible={option.rollback_possible} "
            f"safety_score={option.safety_score:.3f}"
        ),
    )


def _noop_plan(
    *,
    hypothesis_id: str | None,
    target_ref: str,
    rationale: str,
    notes: str,
) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        remediation_option_id=None,
        risk=RiskLevel.LOW,
        actions=[
            FixAction(
                action_type=FixActionType.NOOP,
                target=target_ref,
                params={"action": "noop_investigate"},
                rationale=rationale,
            )
        ],
        notes=notes,
    )


def decide_remediation(state: IncidentState) -> RemediationDecision:
    """
    Generate remediation candidates from verified hypotheses, score them for
    heuristic suitability + safety, and emit a FixPlan for the winner.

    Suitability is not a probability of success. Decision only — does not execute.
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before decide_remediation()")

    target_ref = str(state.observations.extra.get("target_ref", "<workload>"))

    # Focus on the highest-belief hypotheses (already posterior-ranked after verify).
    focus = list(state.hypotheses[: max(1, min(3, len(state.hypotheses)))])

    scored: list[tuple[RemediationOption, RemediationTemplate]] = []
    seq = 0
    seen_actions: set[str] = set()
    for hyp in focus:
        templates = templates_for(cause=hyp.description, remediation_key=hyp.remediation_key)
        for template in templates:
            # Deduplicate identical actions across sibling hypotheses; keep first (higher hyp).
            if template.action in seen_actions:
                continue
            seen_actions.add(template.action)
            seq += 1
            option = _option_from_template(
                template=template,
                hyp=hyp,
                rank=seq,
            )
            scored.append((option, template))

    if not scored:
        hyp0 = state.hypotheses[0]
        plan = _noop_plan(
            hypothesis_id=hyp0.hypothesis_id,
            target_ref=target_ref,
            rationale="No remediation option selected; require human investigation.",
            notes="Remediation decision engine produced no actionable option.",
        )
        return RemediationDecision(options=[], chosen=None, fix_plan=plan, matched_rule=None)

    def _global_rank_key(pair: tuple[RemediationOption, RemediationTemplate]) -> tuple:
        opt = pair[0]
        return (
            opt.suitability,
            opt.safety_score,
            -_LEVEL.get(opt.blast_radius, 0.5),
            -_LEVEL.get(opt.risk.value, 0.5),
        )

    ranked_all = sorted(scored, key=_global_rank_key, reverse=True)
    top_suitability = ranked_all[0][0].suitability
    effective_floor = max(_MIN_VIABLE_SUITABILITY, top_suitability - _EFFECTIVENESS_MARGIN)
    effective = [pair for pair in ranked_all if pair[0].suitability >= effective_floor]
    if not effective:
        display = [opt for opt, _ in ranked_all]
        blocked_plan = _noop_plan(
            hypothesis_id=state.chosen_hypothesis_id,
            target_ref=target_ref,
            rationale=(
                "No remediation option met minimum effectiveness; "
                "collect more evidence or escalate for human approval."
            ),
            notes=(
                f"No remediation option met effectiveness floor "
                f"({effective_floor:.3f}); highest={top_suitability:.3f}."
            ),
        )
        return RemediationDecision(
            options=display,
            chosen=None,
            fix_plan=blocked_plan,
            matched_rule=_BLOCKED_INEFFECTIVE_RULE,
        )

    def _effective_rank_key(pair: tuple[RemediationOption, RemediationTemplate]) -> tuple:
        opt = pair[0]
        return (
            opt.safety_score,
            -_LEVEL.get(opt.blast_radius, 0.5),
            -_LEVEL.get(opt.risk.value, 0.5),
            opt.suitability,
        )

    ranked_effective = sorted(effective, key=_effective_rank_key, reverse=True)

    # Re-order displayed options: chosen family first (safest among effective),
    # then remaining by global suitability+safety.
    chosen_ids = {opt.option_id for opt, _ in ranked_effective}
    display = [opt for opt, _ in ranked_effective] + [
        opt for opt, _ in ranked_all if opt.option_id not in chosen_ids
    ]

    chosen, chosen_template = ranked_effective[0]
    fix_plan = _plan_from_template(
        template=chosen_template,
        option=chosen,
        target_ref=target_ref,
        state=state,
    )
    return RemediationDecision(
        options=display,
        chosen=chosen,
        fix_plan=fix_plan,
        matched_rule=f"remediation_decision.v1:{chosen.action}",
    )
