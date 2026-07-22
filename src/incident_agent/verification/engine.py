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

_MEMORY_GROWTH_RE = re.compile(
    r"(?:memory|rss|heap)?\s*(?:increased|grew|from)?\s*(\d+)\s*mi\b.*?\b(\d+)\s*mi\b",
    re.IGNORECASE,
)

# Bayes factors: prior odds × factor → posterior odds (then renormalize across set).
_BF_CONFIRMED_STRONG = 4.0
_BF_CONFIRMED = 2.5
_BF_INCONCLUSIVE = 0.85
_BF_CONTRADICTED = 0.25


@dataclass(frozen=True, slots=True)
class _Challenge:
    expected: tuple[str, ...]
    observed: tuple[str, ...]
    result: str
    bayes_factor: float


def _observation_lines(state: IncidentState) -> list[str]:
    lines: list[str] = []
    obs = state.observations
    lines.extend(obs.logs)
    lines.extend(obs.events)
    if state.diagnosis is not None:
        lines.extend(e.text for e in state.diagnosis.evidence)
        lines.append(state.diagnosis.summary)
        lines.append(state.diagnosis.category)
    for hyp in state.hypotheses:
        lines.extend(e.text for e in hyp.evidence)
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


def _memory_growth_observations(lines: list[str], observations: Observations) -> list[str]:
    found: list[str] = []
    for line in lines:
        match = _MEMORY_GROWTH_RE.search(line)
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
    support = _collect_hits(lines, spec.support_patterns)
    contradict = _collect_hits(lines, spec.contradict_patterns)

    if hyp.description == "Memory leak":
        support.extend(_memory_growth_observations(lines, observations))

    # Dedup support after growth merge.
    deduped_support: list[str] = []
    seen: set[str] = set()
    for item in support:
        if item not in seen:
            seen.add(item)
            deduped_support.append(item)

    if deduped_support and not contradict:
        strong = len(deduped_support) >= 2 or any("increased from" in s.lower() for s in deduped_support)
        return _Challenge(
            expected=spec.expected_evidence,
            observed=tuple(deduped_support[:4]),
            result="confirmed",
            bayes_factor=_BF_CONFIRMED_STRONG if strong else _BF_CONFIRMED,
        )
    if contradict and not deduped_support:
        return _Challenge(
            expected=spec.expected_evidence,
            observed=tuple(contradict[:4]),
            result="contradicted",
            bayes_factor=_BF_CONTRADICTED,
        )
    if contradict and deduped_support:
        # Mixed signals: do not reward; lean against the hypothesis.
        return _Challenge(
            expected=spec.expected_evidence,
            observed=tuple((deduped_support + contradict)[:4]),
            result="inconclusive",
            bayes_factor=0.5,
        )
    return _Challenge(
        expected=spec.expected_evidence,
        observed=(),
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

    Returns verifications and re-ranked hypotheses with updated likelihoods.
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before verify_hypotheses()")

    lines = _observation_lines(state)
    verifications: list[HypothesisVerification] = []
    raw_posteriors: list[float] = []

    for hyp in state.hypotheses:
        spec = spec_for(hyp.description)
        challenge = _challenge(hyp, spec, lines, state.observations)
        prior = float(hyp.likelihood)
        posterior = _from_odds(_to_odds(prior) * challenge.bayes_factor)
        delta = round(posterior - prior, 4)
        raw_posteriors.append(posterior)
        verifications.append(
            HypothesisVerification(
                hypothesis_id=hyp.hypothesis_id,
                hypothesis=hyp.description,
                expected_evidence=list(challenge.expected),
                observed_evidence=list(challenge.observed),
                result=challenge.result,  # type: ignore[arg-type]
                confidence_delta=delta,
            )
        )

    normalized = _normalize(raw_posteriors)
    updated: list[Hypothesis] = []
    for hyp, posterior in zip(state.hypotheses, normalized, strict=True):
        updated.append(
            hyp.model_copy(update={"likelihood": round(float(posterior), 4)})
        )

    ranked = sorted(updated, key=lambda h: h.likelihood, reverse=True)
    by_id = {v.hypothesis_id: v for v in verifications}
    ordered_verifications = [by_id[h.hypothesis_id] for h in ranked]
    return ordered_verifications, ranked
