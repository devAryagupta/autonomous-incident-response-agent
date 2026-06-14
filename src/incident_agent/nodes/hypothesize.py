from __future__ import annotations

import re
from dataclasses import dataclass

from incident_agent.contracts import Alert, Diagnosis, Evidence, Hypothesis


@dataclass(frozen=True, slots=True)
class _Candidate:
    cause: str
    slug: str
    # keyword patterns that increase likelihood when found in logs/diagnosis
    patterns: tuple[re.Pattern[str], ...]
    base: float


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), flags=re.IGNORECASE)


_CANDIDATES: list[_Candidate] = [
    _Candidate(
        cause="Missing Secret",
        slug="missing_secret",
        patterns=(
            _rx(r"\bsecret\b.*\bnot found\b", r"failedmount.*secret", r"no such file.*secrets"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Missing Environment Variable",
        slug="missing_env_var",
        patterns=(
            _rx(r"environment variable.*missing", r"\b(env|ENV)_[A-Z0-9_]+\b.*missing"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Missing ConfigMap",
        slug="missing_configmap",
        patterns=(
            _rx(r"\bconfigmap\b.*\bnot found\b", r"failedmount.*configmap"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Dependency Unavailable (DNS/Network)",
        slug="dependency_unavailable",
        patterns=(
            _rx(r"no such host", r"lookup .*: no such host", r"connection refused", r"i/o timeout"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Invalid Image Tag / Image Pull Error",
        slug="invalid_image",
        patterns=(
            _rx(r"imagepullbackoff", r"errimagepull", r"manifest unknown", r"not found: manifest"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Application Bug / Unhandled Exception",
        slug="app_bug_unhandled_exception",
        patterns=(
            _rx(r"traceback", r"unhandled exception", r"panic:", r"segmentation fault"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Resource Constraint (OOMKilled)",
        slug="resource_constraint_oom",
        patterns=(
            _rx(r"oomkilled", r"\bkilled\b"),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Misconfigured Volume Mount / Missing Path",
        slug="misconfigured_volume_mount",
        patterns=(
            _rx(r"failedmount", r"no such file or directory", r"cannot open .*: no such file"),
        ),
        base=0.06,
    ),
]


def _collect_evidence_lines(
    text_lines: list[str],
    pat: re.Pattern[str],
    *,
    limit: int = 3,
) -> list[str]:
    out: list[str] = []
    for line in text_lines:
        if pat.search(line):
            out.append(line.strip())
            if len(out) >= limit:
                break
    return out


def _normalize(scores: list[float]) -> list[float]:
    total = sum(scores)
    if total <= 0:
        # fallback: uniform distribution
        return [1.0 / len(scores)] * len(scores) if scores else []
    return [s / total for s in scores]


def hypothesize(
    *,
    alert: Alert,
    logs: list[str],
    diagnosis: Diagnosis,
    top_n: int = 3,
) -> list[Hypothesis]:
    """
    Generate *multiple* hypotheses with probabilities.

    Design constraints:
    - No external dependencies (no LLM, no K8s/Prometheus).
    - Deterministic and testable.
    - Always returns Top-N hypotheses and never a single answer (N is clamped to >= 2).
    """
    _ = alert  # reserved for future use (e.g., alert labels/annotations)

    n = max(2, top_n)
    joined_context_lines = [diagnosis.summary] + logs

    raw_scores: list[float] = []
    evidence_by_slug: dict[str, list[str]] = {}

    for cand in _CANDIDATES:
        score = cand.base
        evidence_lines: list[str] = []
        for pat in cand.patterns:
            ev = _collect_evidence_lines(joined_context_lines, pat, limit=3)
            if ev:
                # pattern hit => boost this candidate
                score += 0.35
                evidence_lines.extend(ev)

        # de-dup evidence while preserving order
        deduped: list[str] = []
        seen: set[str] = set()
        for line in evidence_lines:
            if line not in seen:
                seen.add(line)
                deduped.append(line)

        evidence_by_slug[cand.slug] = deduped
        raw_scores.append(score)

    probs = _normalize(raw_scores)

    ranked = sorted(
        zip(_CANDIDATES, probs, strict=True),
        key=lambda t: t[1],
        reverse=True,
    )[: min(n, len(_CANDIDATES))]

    hypotheses: list[Hypothesis] = []
    for rank, (cand, p) in enumerate(ranked, start=1):
        ev_lines = evidence_by_slug.get(cand.slug, [])
        fallback = ["No direct keyword match; included as uncertainty"]
        evidence_lines = ev_lines if ev_lines else fallback
        evidence = [Evidence(source="logs", text=line) for line in evidence_lines]
        hypotheses.append(
            Hypothesis(
                hypothesis_id=f"h{rank}-{cand.slug}",
                description=cand.cause,
                likelihood=round(float(p), 4),
                evidence=evidence,
            )
        )

    # Guarantee >=2 hypotheses even if candidate list changes.
    if len(hypotheses) < 2:
        raise RuntimeError("Hypothesis generation must return at least 2 hypotheses.")

    return hypotheses

