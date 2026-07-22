"""Remediation Decision Engine: rank safe options by blast radius / reversibility."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import (
    FixAction,
    FixActionType,
    FixPlan,
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    RemediationOption,
    RiskLevel,
)
from incident_agent.remediation.options import RemediationTemplate, templates_for

_LEVEL = {"low": 0.0, "medium": 0.5, "high": 1.0}

# Prefer minimum blast radius, then undoability, then confidence of success.
_W_BLAST = 0.30
_W_REVERSIBILITY = 0.25
_W_ROLLBACK = 0.15
_W_RISK = 0.15
_W_CONFIDENCE = 0.15

# Options below this confidence are not considered "solutions" (avoids
# low-blast / low-effect actions like restart winning over a real fix).
_MIN_VIABLE_CONFIDENCE = 0.40


@dataclass(frozen=True, slots=True)
class RemediationDecision:
    options: list[RemediationOption]
    chosen: RemediationOption | None
    fix_plan: FixPlan
    matched_rule: str | None


def _verification_factor(
    hyp: Hypothesis,
    verifications: list[HypothesisVerification],
) -> float:
    match = next((v for v in verifications if v.hypothesis_id == hyp.hypothesis_id), None)
    if match is None:
        return 0.85
    if match.result == "confirmed":
        return 1.0
    if match.result == "contradicted":
        return 0.40
    return 0.75


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _safety_score(
    *,
    blast_radius: str,
    reversibility: str,
    rollback_possible: bool,
    risk: RiskLevel,
    confidence: float,
) -> float:
    blast = _LEVEL.get(blast_radius, 0.5)
    rev = _LEVEL.get(reversibility, 0.5)
    risk_v = _LEVEL.get(risk.value, 0.5)
    score = (
        (1.0 - blast) * _W_BLAST
        + rev * _W_REVERSIBILITY
        + (1.0 if rollback_possible else 0.0) * _W_ROLLBACK
        + (1.0 - risk_v) * _W_RISK
        + confidence * _W_CONFIDENCE
    )
    return round(_clamp(score), 4)


def _option_from_template(
    *,
    template: RemediationTemplate,
    hyp: Hypothesis,
    rank: int,
    verifications: list[HypothesisVerification],
) -> RemediationOption:
    v_factor = _verification_factor(hyp, verifications)
    confidence = round(_clamp(template.base_confidence * float(hyp.likelihood) * v_factor), 4)
    safety = _safety_score(
        blast_radius=template.blast_radius,
        reversibility=template.reversibility,
        rollback_possible=template.rollback_possible,
        risk=template.risk,
        confidence=confidence,
    )
    return RemediationOption(
        option_id=f"r{rank}-{template.action}",
        action=template.action,
        expected_effect=template.expected_effect,
        risk=template.risk,
        blast_radius=template.blast_radius,  # type: ignore[arg-type]
        reversibility=template.reversibility,  # type: ignore[arg-type]
        rollback_possible=template.rollback_possible,
        confidence=confidence,
        safety_score=safety,
        hypothesis_id=hyp.hypothesis_id,
        rationale=template.rationale,
    )


def _plan_from_template(
    *,
    template: RemediationTemplate,
    option: RemediationOption,
    target_ref: str,
) -> FixPlan:
    params: dict = {"change": template.change, "action": template.action}
    if template.commands:
        params["commands"] = list(template.commands)
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
            f"Chosen remediation={option.action} blast_radius={option.blast_radius} "
            f"reversibility={option.reversibility} rollback_possible={option.rollback_possible} "
            f"safety_score={option.safety_score:.3f}"
        ),
    )


def _noop_plan(*, hypothesis_id: str | None, target_ref: str) -> FixPlan:
    return FixPlan(
        hypothesis_id=hypothesis_id,
        remediation_option_id=None,
        risk=RiskLevel.LOW,
        actions=[
            FixAction(
                action_type=FixActionType.NOOP,
                target=target_ref,
                params={},
                rationale="No remediation option selected; require human investigation.",
            )
        ],
        notes="Remediation decision engine produced no actionable option.",
    )


def decide_remediation(state: IncidentState) -> RemediationDecision:
    """
    Generate remediation candidates from verified hypotheses, score them for
    minimum blast radius / high reversibility, and emit a FixPlan for the winner.

    Decision only — does not execute.
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before decide_remediation()")

    target_ref = str(state.observations.extra.get("target_ref", "<workload>"))
    verifications = list(state.hypothesis_verifications)

    # Focus on top hypotheses (already posterior-ranked).
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
                verifications=verifications,
            )
            scored.append((option, template))

    if not scored:
        hyp0 = state.hypotheses[0]
        plan = _noop_plan(hypothesis_id=hyp0.hypothesis_id, target_ref=target_ref)
        return RemediationDecision(options=[], chosen=None, fix_plan=plan, matched_rule=None)

    def _rank_key(pair: tuple[RemediationOption, RemediationTemplate]) -> tuple:
        opt = pair[0]
        return (
            opt.safety_score,
            -_LEVEL.get(opt.blast_radius, 0.5),
            opt.confidence,
            -_LEVEL.get(opt.risk.value, 0.5),
        )

    ranked_all = sorted(scored, key=_rank_key, reverse=True)
    viable = [pair for pair in ranked_all if pair[0].confidence >= _MIN_VIABLE_CONFIDENCE]
    ranked = viable if viable else ranked_all

    # Re-order displayed options: chosen family first by decision rank among viable,
    # then remaining by global safety (still show low-confidence alternatives).
    chosen_ids = {opt.option_id for opt, _ in ranked}
    display = [opt for opt, _ in ranked] + [
        opt for opt, _ in ranked_all if opt.option_id not in chosen_ids
    ]

    chosen, chosen_template = ranked[0]
    fix_plan = _plan_from_template(
        template=chosen_template,
        option=chosen,
        target_ref=target_ref,
    )
    return RemediationDecision(
        options=display,
        chosen=chosen,
        fix_plan=fix_plan,
        matched_rule=f"remediation_decision.v1:{chosen.action}",
    )
