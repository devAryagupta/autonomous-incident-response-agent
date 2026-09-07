"""Evidence-driven hypothesis engine: diagnosis + observations → ranked hypotheses."""

from __future__ import annotations

import re

from incident_agent.contracts import Diagnosis, Evidence, Hypothesis, IncidentState
from incident_agent.hypothesis.candidates import CANDIDATES_BY_CATEGORY, HypothesisCandidate
from incident_agent.memory.priors import prior_adjustments_from_memory

_PATTERN_BOOST = 0.35
_DIAGNOSIS_EVIDENCE_BOOST = 0.10
_MEMORY_EVIDENCE_NOTE = "Historical incident memory supports this cause"

_OOM_TERMS = (
    "oom",
    "oomkilled",
    "exit code 137",
)
_CONFIGURATION_TERMS = (
    "missing environment variable",
    "keyerror",
    "configuration missing",
    "yaml parse error",
    "invalid configuration",
    "secret missing",
    "failedmount",
)
_OOM_PATTERN = re.compile(r"\boomkilled\b|\bexit (?:code|status) 137\b")
_TRAFFIC_SIGNALS = (
    "traffic spike",
    "rps increased",
    "qps increased",
    "request rate increased",
    "high request volume",
)
_STARTUP_SIGNALS = (
    "startup",
    "during start",
)

# function to normalize the score by dividing the score by the total score.
def _normalize(scores: list[float]) -> list[float]:
    total = sum(scores)
    if total <= 0:
        return [1.0 / len(scores)] * len(scores) if scores else []
    return [s / total for s in scores]

# Purpose of this function is to get the context lines from the state. Reading the diagnosis summary, category, evidence, logs, events, metrics.
def _context_lines(state: IncidentState) -> list[str]:
    diagnosis = state.diagnosis
    lines: list[str] = []
    if diagnosis is not None:
        lines.append(diagnosis.summary)
        lines.append(diagnosis.category)
        lines.extend(e.text for e in diagnosis.evidence)
    lines.extend(state.observations.logs)
    lines.extend(state.observations.events)
    metrics = state.observations.extra.get("metrics")
    if isinstance(metrics, dict):
        lines.extend(f"{k}={v}" for k, v in metrics.items() if isinstance(v, (str, int, float)))
    return [line for line in lines if line]

# Purpose of this function is to collect the hits from the list of lines that given by the output of the _context_lines function.
def _collect_hits(lines: list[str], pat: re.Pattern[str], *, limit: int = 3) -> list[str]:
    out: list[str] = []
    for line in lines:
        if pat.search(line):
            cleaned = line.strip()
            if cleaned and cleaned not in out:
                out.append(cleaned)
            if len(out) >= limit:
                break
    return out

def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _infer_category(diagnosis: Diagnosis, context: list[str]) -> str:
    """Infer a category when the diagnosis does not provide one explicitly."""
    if diagnosis.category and diagnosis.category != "Unknown":
        return diagnosis.category

    summary = diagnosis.summary.lower()
    joined_context = "\n".join(context).lower()

    if _contains_any(summary, _OOM_TERMS) or _contains_any(joined_context, _OOM_TERMS):
        return "OOMKilled"
    if _contains_any(joined_context, _CONFIGURATION_TERMS):
        return "Invalid Configuration"
    return "Application Failure"

# Unnormalized heuristic weight: catalog prior + pattern/memory boosts.
# After ranking we normalize so Hypothesis.likelihood is a prior belief.
def _score_candidate(
    cand: HypothesisCandidate,
    context: list[str],
    diagnosis: Diagnosis,
) -> tuple[float, list[str]]:
    score = cand.prior
    evidence_lines: list[str] = []

    for pattern in cand.patterns:
        hits = _collect_hits(context, pattern, limit=3)
        if hits:
            score += _PATTERN_BOOST
            evidence_lines.extend(hits)

    for item in diagnosis.evidence:
        text = item.text.strip()
        if not text:
            continue
        for pattern in cand.patterns:
            if pattern.search(text):
                score += _DIAGNOSIS_EVIDENCE_BOOST
                if text not in evidence_lines:
                    evidence_lines.append(text)
                break

    deduped: list[str] = []
    seen: set[str] = set()
    for line in evidence_lines:
        if line not in seen:
            seen.add(line)
            deduped.append(line)

    if not deduped:
        deduped = list(cand.default_evidence)
    return score, deduped


def _oom_prior_adjust(context: list[str]) -> dict[str, float]:
    """Heuristic score adjustments for competing OOM causes."""
    joined = "\n".join(context).lower()
    oom_mentions = len(_OOM_PATTERN.findall(joined))

    adjustments = {
        "memory_leak": 0.0,
        "memory_limit_too_low": 0.0,
        "traffic_spike": 0.0,
    }

    repeated_oom = oom_mentions >= 2
    restart_loop = "backoff" in joined
    startup_oom = any(signal in joined for signal in _STARTUP_SIGNALS)
    traffic_spike = any(signal in joined for signal in _TRAFFIC_SIGNALS)

    if repeated_oom or restart_loop:
        adjustments["memory_leak"] += 0.20
        adjustments["memory_limit_too_low"] -= 0.05

    if startup_oom:
        adjustments["memory_limit_too_low"] += 0.15
        adjustments["memory_leak"] -= 0.05

    if traffic_spike:
        adjustments["traffic_spike"] += 0.25

    return adjustments


def _evidence_with_source(state: IncidentState, lines: list[str]) -> list[Evidence]:
    log_set = set(state.observations.logs)
    event_set = set(state.observations.events)
    out: list[Evidence] = []
    for line in lines[:3]:
        if line in event_set:
            out.append(Evidence(source="events", text=line))
        elif line in log_set:
            out.append(Evidence(source="logs", text=line))
        else:
            out.append(Evidence(source="other", text=line))
    return out


_Scored = tuple[HypothesisCandidate, float, list[str]]


def _candidates_for(diagnosis: Diagnosis, context: list[str]) -> tuple[str, tuple[HypothesisCandidate, ...]]:
    category = _infer_category(diagnosis, context)
    candidates = CANDIDATES_BY_CATEGORY.get(category, CANDIDATES_BY_CATEGORY["Unknown"])
    return category, candidates


def _score_adjustments(
    *,
    category: str,
    context: list[str],
    candidates: tuple[HypothesisCandidate, ...],
    similar_incidents: list[object],
) -> tuple[dict[str, float], dict[str, float]]:
    oom = _oom_prior_adjust(context) if category == "OOMKilled" else {}
    memory = prior_adjustments_from_memory(
        candidate_slugs=[c.slug for c in candidates],
        similar_incidents=list(similar_incidents),
    )
    return oom, memory


def _apply_adjustments(
    cand: HypothesisCandidate,
    raw: float,
    evidence_lines: list[str],
    *,
    oom: dict[str, float],
    memory: dict[str, float],
) -> _Scored:
    raw += oom.get(cand.slug, 0.0)
    mem_boost = memory.get(cand.slug, 0.0)
    if mem_boost > 0:
        raw += mem_boost
        if _MEMORY_EVIDENCE_NOTE not in evidence_lines:
            evidence_lines = [*evidence_lines, _MEMORY_EVIDENCE_NOTE]
    return cand, max(raw, 0.01), evidence_lines


def _score_candidates(
    candidates: tuple[HypothesisCandidate, ...],
    *,
    context: list[str],
    diagnosis: Diagnosis,
    oom: dict[str, float],
    memory: dict[str, float],
) -> list[_Scored]:
    scored: list[_Scored] = []
    for cand in candidates:
        raw, evidence_lines = _score_candidate(cand, context, diagnosis)
        scored.append(_apply_adjustments(cand, raw, evidence_lines, oom=oom, memory=memory))
    return scored


def _rank_top(scored: list[_Scored], *, top_n: int) -> list[_Scored]:
    probs = _normalize([raw for _, raw, _ in scored])
    ranked = sorted(
        [
            (cand, prob, evidence_lines)
            for (cand, _raw, evidence_lines), prob in zip(scored, probs, strict=True)
        ],
        key=lambda item: item[1],
        reverse=True,
    )
    return ranked[: min(top_n, len(scored))]


def _to_hypotheses(state: IncidentState, ranked: list[_Scored]) -> list[Hypothesis]:
    top_probs = _normalize([prob for _, prob, _ in ranked])
    hypotheses: list[Hypothesis] = []
    for rank, ((cand, _prob, evidence_lines), prior_belief) in enumerate(
        zip(ranked, top_probs, strict=True),
        start=1,
    ):
        hypotheses.append(
            Hypothesis(
                hypothesis_id=f"h{rank}-{cand.slug}",
                description=cand.cause,
                likelihood=round(float(prior_belief), 4),
                evidence=_evidence_with_source(state, evidence_lines),
                verification_checks=list(cand.verification_checks),
                remediation_key=cand.remediation_key,
            )
        )
    return hypotheses


def hypothesize_from_state(state: IncidentState) -> list[Hypothesis]:
    """
    Build ranked root-cause hypotheses from diagnosis + observation evidence.

    Diagnosis names the symptom; hypotheses explain underlying causes.
    ``Hypothesis.likelihood`` here is a normalized *prior belief* (sums to 1).
    ``verify_hypotheses`` later overwrites it with posterior belief.
    """
    if state.diagnosis is None:
        raise ValueError("state.diagnosis is required before hypothesize()")

    top_n = max(2, int(state.observations.extra.get("top_n", 3)))
    context = _context_lines(state)
    category, candidates = _candidates_for(state.diagnosis, context)
    oom, memory = _score_adjustments(
        category=category,
        context=context,
        candidates=candidates,
        similar_incidents=list(state.similar_incidents),
    )
    scored = _score_candidates(
        candidates,
        context=context,
        diagnosis=state.diagnosis,
        oom=oom,
        memory=memory,
    )
    hypotheses = _to_hypotheses(state, _rank_top(scored, top_n=top_n))
    if len(hypotheses) < 2:
        raise RuntimeError("Hypothesis generation must return at least 2 hypotheses.")
    return hypotheses
