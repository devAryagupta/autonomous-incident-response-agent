"""Evidence-driven hypothesis engine: diagnosis + observations → ranked hypotheses."""

from __future__ import annotations

import re

from incident_agent.contracts import Diagnosis, Evidence, Hypothesis, IncidentState
from incident_agent.hypothesis.candidates import CANDIDATES_BY_CATEGORY, HypothesisCandidate

_PATTERN_BOOST = 0.35
_DIAGNOSIS_EVIDENCE_BOOST = 0.10


def _normalize(scores: list[float]) -> list[float]:
    total = sum(scores)
    if total <= 0:
        return [1.0 / len(scores)] * len(scores) if scores else []
    return [s / total for s in scores]


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


def _infer_category(diagnosis: Diagnosis, context: list[str]) -> str:
    if diagnosis.category and diagnosis.category != "Unknown":
        return diagnosis.category

    joined = "\n".join(context).lower()
    summary = diagnosis.summary.lower()
    if "oom" in summary or "oomkilled" in joined or "exit code 137" in joined:
        return "OOMKilled"
    if any(
        token in joined
        for token in ("errimagepull", "imagepullbackoff", "manifest unknown", "failed to pull")
    ) or "image" in summary:
        return "Invalid Image"
    if ("secret" in joined and "not found" in joined) or "missing secret" in summary:
        return "Missing Secret"
    if any(token in joined for token in ("traceback", "unhandled exception", "panic:")):
        return "Application Crash"
    return "Unknown"


def _score_candidate(
    cand: HypothesisCandidate,
    context: list[str],
    diagnosis: Diagnosis,
) -> tuple[float, list[str]]:
    score = cand.prior
    evidence_lines: list[str] = []

    for pat in cand.patterns:
        hits = _collect_hits(context, pat, limit=3)
        if hits:
            score += _PATTERN_BOOST
            evidence_lines.extend(hits)

    for item in diagnosis.evidence:
        text = item.text.strip()
        if not text:
            continue
        for pat in cand.patterns:
            if pat.search(text):
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
    """SRE-style prior tilt for OOM: leak vs low limit vs traffic."""
    joined = "\n".join(context).lower()
    oom_mentions = len(re.findall(r"oomkilled|exit code 137|exit status 137", joined))
    adjustments = {
        "memory_leak": 0.0,
        "memory_limit_too_low": 0.0,
        "traffic_spike": 0.0,
    }

    if oom_mentions >= 2 or "restart" in joined or "backoff" in joined:
        adjustments["memory_leak"] += 0.20
        adjustments["memory_limit_too_low"] -= 0.05
    if "startup" in joined or "during start" in joined:
        adjustments["memory_limit_too_low"] += 0.15
        adjustments["memory_leak"] -= 0.05
    if any(token in joined for token in ("traffic", "spike", "rps", "qps", "requests")):
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


def hypothesize_from_state(state: IncidentState) -> list[Hypothesis]:
    """
    Build ranked root-cause hypotheses from diagnosis + observation evidence.

    Diagnosis names the symptom; hypotheses explain underlying causes.
    Always returns at least two hypotheses with normalized likelihoods.
    """
    if state.diagnosis is None:
        raise ValueError("state.diagnosis is required before hypothesize()")

    top_n = max(2, int(state.observations.extra.get("top_n", 3)))
    context = _context_lines(state)
    category = _infer_category(state.diagnosis, context)
    candidates = CANDIDATES_BY_CATEGORY.get(category, CANDIDATES_BY_CATEGORY["Unknown"])
    adjustments = _oom_prior_adjust(context) if category == "OOMKilled" else {}

    scored: list[tuple[HypothesisCandidate, float, list[str]]] = []
    for cand in candidates:
        raw, evidence_lines = _score_candidate(cand, context, state.diagnosis)
        raw += adjustments.get(cand.slug, 0.0)
        scored.append((cand, max(raw, 0.01), evidence_lines))

    probs = _normalize([raw for _, raw, _ in scored])
    ranked = sorted(
        [
            (cand, prob, evidence_lines)
            for (cand, _raw, evidence_lines), prob in zip(scored, probs, strict=True)
        ],
        key=lambda item: item[1],
        reverse=True,
    )[: min(top_n, len(scored))]

    top_probs = _normalize([prob for _, prob, _ in ranked])
    hypotheses: list[Hypothesis] = []
    for rank, ((cand, _prob, evidence_lines), likelihood) in enumerate(
        zip(ranked, top_probs, strict=True),
        start=1,
    ):
        hypotheses.append(
            Hypothesis(
                hypothesis_id=f"h{rank}-{cand.slug}",
                description=cand.cause,
                likelihood=round(float(likelihood), 4),
                evidence=_evidence_with_source(state, evidence_lines),
                verification_checks=list(cand.verification_checks),
                remediation_key=cand.remediation_key,
            )
        )

    if len(hypotheses) < 2:
        raise RuntimeError("Hypothesis generation must return at least 2 hypotheses.")
    return hypotheses
