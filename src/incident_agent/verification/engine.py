"""Challenge hypotheses against observations and update posteriors (Bayesian-style)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from incident_agent.contracts import (
    Hypothesis,
    HypothesisVerification,
    IncidentState,
    Observations,
)
from incident_agent.verification.specs import VerificationSpec, spec_for

# THIS REGEX WILL BE USED TO MATCH THE MEMORY GROWTH OBSERVATIONS
_MEMORY_GROWTH_REGEX = re.compile(
    r"(?:memory|rss|heap)?\s*(?:increased|grew|from)?\s*(\d+)\s*mi\b.*?\b(\d+)\s*mi\b",
    re.IGNORECASE,
)

# Bayes factors: prior odds × factor → posterior odds (then renormalize across set).
_BF_CONFIRMED_STRONG = 6.0
_BF_CONFIRMED = 2.5
_BF_INCONCLUSIVE = 0.85
_BF_MIXED = 0.65
_BF_CONTRADICTED = 0.20


@dataclass(frozen=True, slots=True)
class _Challenge:
    supporting_expected: tuple[str, ...]
    contradicting_expected: tuple[str, ...]
    required_expected: tuple[str, ...]
    observed_supporting: tuple[str, ...]
    observed_contradicting: tuple[str, ...]
    observed_required: tuple[str, ...]
    result: str
    bayes_factor: float


def _observation_lines(state: IncidentState) -> list[str]:
    """
    Verification inputs must be observation-derived only.

    Allowed:
    - raw logs/events
    - provider-collected summaries already merged into observations
    - provider metrics in observations.extra["metrics"]

    Not allowed:
    - hypothesis-derived evidence/defaults
    - diagnosis labels/summaries (derived interpretation)
    """
    lines: list[str] = []
    obs = state.observations
    lines.extend(obs.logs)
    lines.extend(obs.events)
    metrics = obs.extra.get("metrics")
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            lines.append(f"{key}={value}")
            if isinstance(value, dict):
                for nested_k, nested_v in value.items():
                    lines.append(f"{nested_k}={nested_v}")
    return [line for line in lines if isinstance(line, str) and line.strip()]


def _collect_hits(lines: list[str], patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    hits: list[str] = []
    seen: set[str] = set()
    for line in lines:
        for pat in patterns:
            if pat.search(line):
                cleaned = line.strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    hits.append(cleaned)
                break
    return hits


def _collect_required_hits(
    lines: list[str],
    patterns: tuple[re.Pattern[str], ...],
) -> tuple[list[str], int]:
    hits: list[str] = []
    seen: set[str] = set()
    matched_patterns = 0
    for pat in patterns:
        for line in lines:
            if pat.search(line):
                matched_patterns += 1
                cleaned = line.strip()
                if cleaned and cleaned not in seen:
                    seen.add(cleaned)
                    hits.append(cleaned)
                break
    return hits, matched_patterns


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _memory_growth_observations(lines: list[str], observations: Observations) -> list[str]:
    found: list[str] = []
    for line in lines:
        match = _MEMORY_GROWTH_REGEX.search(line)
        if match:
            low, high = int(match.group(1)), int(match.group(2))
            if high > low:
                found.append(f"Memory increased from {low}Mi to {high}Mi")
    series = observations.extra.get("metrics", {})
    if isinstance(series, dict):
        mem = series.get("memory_mi") or series.get("memory") or series.get("series", {})
        if isinstance(mem, dict):
            start = mem.get("start") or mem.get("min")
            end = mem.get("end") or mem.get("max") or mem.get("peak")
            if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
                found.append(f"Memory increased from {int(start)}Mi to {int(end)}Mi")
        elif isinstance(mem, list) and len(mem) >= 2:
            try:
                start_v, end_v = float(mem[0]), float(mem[-1])
            except (TypeError, ValueError):
                start_v = end_v = 0.0
            if end_v > start_v:
                found.append(f"Memory increased from {int(start_v)}Mi to {int(end_v)}Mi")
    # Dedup
    out: list[str] = []
    seen: set[str] = set()
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _challenge(
    hyp: Hypothesis,
    spec: VerificationSpec,
    lines: list[str],
    observations: Observations,
) -> _Challenge:
    # collect the hits for the support and contradict patterns and required patterns
    support = _collect_hits(lines, spec.support_patterns)
    contradict = _collect_hits(lines, spec.contradict_patterns)
    required, required_matches = _collect_required_hits(lines, spec.required_patterns)
    # if the hypothesis is a memory leak, collect the memory growth observations and add them to the support and required patterns
    if hyp.description == "Memory leak":
        growth = _memory_growth_observations(lines, observations)
        support.extend(growth)
        required.extend(growth)
        if growth and spec.required_patterns:
            required_matches = max(required_matches, 1)

    deduped_support = _dedupe(support)
    deduped_contradict = _dedupe(contradict)
    deduped_required = _dedupe(required)
    has_support = bool(deduped_support)
    has_contradict = bool(deduped_contradict)
    has_required_signal = bool(deduped_required)
    required_total = len(spec.required_patterns)
    required_satisfied = required_total > 0 and required_matches >= required_total

    if required_satisfied and not has_contradict:
        strong = len(deduped_required) >= 2 or len(deduped_support) >= 2
        return _Challenge(
            supporting_expected=spec.supporting_evidence,
            contradicting_expected=spec.contradicting_evidence,
            required_expected=spec.required_evidence,
            observed_supporting=tuple(deduped_support[:4]),
            observed_contradicting=tuple(deduped_contradict[:4]),
            observed_required=tuple(deduped_required[:4]),
            result="confirmed",
            bayes_factor=_BF_CONFIRMED_STRONG if strong else (_BF_CONFIRMED + 1.0),
        )
    if has_required_signal and not has_contradict:
        return _Challenge(
            supporting_expected=spec.supporting_evidence,
            contradicting_expected=spec.contradicting_evidence,
            required_expected=spec.required_evidence,
            observed_supporting=tuple(deduped_support[:4]),
            observed_contradicting=tuple(deduped_contradict[:4]),
            observed_required=tuple(deduped_required[:4]),
            result="confirmed",
            bayes_factor=_BF_CONFIRMED,
        )
    if has_support and not has_contradict:
        return _Challenge(
            supporting_expected=spec.supporting_evidence,
            contradicting_expected=spec.contradicting_evidence,
            required_expected=spec.required_evidence,
            observed_supporting=tuple(deduped_support[:4]),
            observed_contradicting=tuple(deduped_contradict[:4]),
            observed_required=tuple(deduped_required[:4]),
            result="confirmed",
            bayes_factor=_BF_CONFIRMED,
        )
    if has_contradict and not has_support and not has_required_signal:
        return _Challenge(
            supporting_expected=spec.supporting_evidence,
            contradicting_expected=spec.contradicting_evidence,
            required_expected=spec.required_evidence,
            observed_supporting=tuple(deduped_support[:4]),
            observed_contradicting=tuple(deduped_contradict[:4]),
            observed_required=tuple(deduped_required[:4]),
            result="contradicted",
            bayes_factor=_BF_CONTRADICTED,
        )
    if has_contradict and (has_support or has_required_signal):
        # Mixed signals: do not reward; lean against the hypothesis.
        return _Challenge(
            supporting_expected=spec.supporting_evidence,
            contradicting_expected=spec.contradicting_evidence,
            required_expected=spec.required_evidence,
            observed_supporting=tuple(deduped_support[:4]),
            observed_contradicting=tuple(deduped_contradict[:4]),
            observed_required=tuple(deduped_required[:4]),
            result="inconclusive",
            bayes_factor=_BF_MIXED,
        )
    return _Challenge(
        supporting_expected=spec.supporting_evidence,
        contradicting_expected=spec.contradicting_evidence,
        required_expected=spec.required_evidence,
        observed_supporting=tuple(deduped_support[:4]),
        observed_contradicting=tuple(deduped_contradict[:4]),
        observed_required=tuple(deduped_required[:4]),
        result="inconclusive",
        bayes_factor=_BF_INCONCLUSIVE,
    )


def _clamp_prob(p: float) -> float:
    return min(max(p, 1e-6), 1.0 - 1e-6)


def _to_odds(p: float) -> float:
    p = _clamp_prob(p)
    return p / (1.0 - p)


def _from_odds(odds: float) -> float:
    return odds / (1.0 + odds)


def _normalize(values: list[float]) -> list[float]:
    total = sum(values)
    if total <= 0:
        return [1.0 / len(values)] * len(values) if values else []
    return [v / total for v in values]


def verify_hypotheses_from_state(
    state: IncidentState,
) -> tuple[list[HypothesisVerification], list[Hypothesis]]:
    """
    Prior belief + observed evidence → posterior belief.

    Reads ``Hypothesis.likelihood`` as the prior, writes the renormalized
    posterior back onto the same field (current belief after evidence).
    Does not estimate remediation success probability.
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before verify_hypotheses()")

    lines = _observation_lines(state)
    verifications: list[HypothesisVerification] = []
    raw_posteriors: list[float] = []

    for hyp in state.hypotheses:
        spec = spec_for(hyp.description) # get the supporting and contradicting evidence and the patterns to verify the evidence
        challenge = _challenge(hyp, spec, lines, state.observations)
        prior_belief = float(hyp.likelihood)
        posterior_belief = _from_odds(_to_odds(prior_belief) * challenge.bayes_factor)
        delta = round(posterior_belief - prior_belief, 4)
        raw_posteriors.append(posterior_belief)
        expected_evidence = list(
            challenge.required_expected
            if challenge.required_expected
            else challenge.supporting_expected
        )
        observed_evidence = _dedupe(
            list(challenge.observed_required)
            + list(challenge.observed_supporting)
            + list(challenge.observed_contradicting)
        )
        verifications.append(
            HypothesisVerification(
                hypothesis_id=hyp.hypothesis_id,
                hypothesis=hyp.description,
                supporting_evidence=list(challenge.supporting_expected),
                contradicting_evidence=list(challenge.contradicting_expected),
                required_evidence=list(challenge.required_expected),
                observed_supporting_evidence=list(challenge.observed_supporting),
                observed_contradicting_evidence=list(challenge.observed_contradicting),
                observed_required_evidence=list(challenge.observed_required),
                expected_evidence=expected_evidence,
                observed_evidence=observed_evidence,
                result=challenge.result,  # type: ignore[arg-type]
                confidence_delta=delta,
            )
        )

    normalized = _normalize(raw_posteriors)
    updated: list[Hypothesis] = []
    for hyp, posterior_belief in zip(state.hypotheses, normalized, strict=True):
        updated.append(
            hyp.model_copy(update={"likelihood": round(float(posterior_belief), 4)})
        )

    ranked = sorted(updated, key=lambda h: h.likelihood, reverse=True)
    by_id = {v.hypothesis_id: v for v in verifications}
    ordered_verifications = [by_id[h.hypothesis_id] for h in ranked]
    return ordered_verifications, ranked
