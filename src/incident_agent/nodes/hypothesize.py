from __future__ import annotations

import re
from dataclasses import dataclass

from incident_agent.contracts import Evidence, Hypothesis, IncidentState


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
            _rx(
                r"imagepullbackoff",
                r"errimagepull",
                r"manifest unknown",
                r"pull access denied",
                r"authentication required",
                r"unauthorized",
                r"failed to pull image",
            ),
        ),
        base=0.06,
    ),
    _Candidate(
        cause="Bad Configuration / Config Parse Error",
        slug="bad_config",
        patterns=(
            _rx(r"failed to load config", r"configerror", r"invalid configuration", r"\byaml:\b", r"\bjson:\b"),
        ),
        base=0.05,
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
        cause="Resource Constraint (Disk Pressure / No Space)",
        slug="disk_pressure",
        patterns=(
            _rx(r"diskpressure", r"ephemeral-storage", r"no space left on device", r"\benospc\b"),
        ),
        base=0.05,
    ),
    _Candidate(
        cause="Database Unavailable",
        slug="db_unavailable",
        patterns=(
            _rx(r"postgres", r"mysql", r"\bdb\b", r"could not connect", r"connection refused.*5432"),
        ),
        base=0.04,
    ),
    _Candidate(
        cause="Redis Unavailable",
        slug="redis_unavailable",
        patterns=(
            _rx(r"\bredis\b", r"connection refused.*6379", r"failed to connect to redis"),
        ),
        base=0.04,
    ),
    _Candidate(
        cause="DNS Resolution Failure",
        slug="dns_failure",
        patterns=(
            _rx(r"no such host", r"\bnxdomain\b", r"servfail", r"dns"),
        ),
        base=0.04,
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
    state: IncidentState,
) -> dict[str, object]:
    """
    Hypothesize node (state-in, partial-state-out).

    Design constraints:
    - No external dependencies (no LLM, no K8s/Prometheus).
    - Deterministic and testable.
    - Always returns Top-N hypotheses and never a single answer (N is clamped to >= 2).
    """
    _ = state.alert  # reserved for future use (e.g., labels/annotations)

    if state.diagnosis is None:
        raise ValueError("state.diagnosis is required before hypothesize()")

    top_n = int(state.observations.extra.get("top_n", 3))

    n = max(2, top_n)
    joined_context_lines = [state.diagnosis.summary] + (
        state.observations.logs + state.observations.events
    )

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

    return {"hypotheses": hypotheses}

