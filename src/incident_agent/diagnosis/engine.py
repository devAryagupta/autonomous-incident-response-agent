"""Deterministic diagnosis engine for scoped CrashLoopBackOff triage."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import Diagnosis, Evidence, Observations
from incident_agent.diagnosis.evidence import ExtractedEvidence, extract_evidence

CATEGORY_OOMKILLED = "OOMKilled"
CATEGORY_INVALID_CONFIGURATION = "Invalid Configuration"
CATEGORY_APPLICATION_FAILURE = "Application Failure"

_SUMMARIES: dict[str, str] = {
    CATEGORY_OOMKILLED: "OOMKilled",
    CATEGORY_INVALID_CONFIGURATION: "Invalid Configuration",
    CATEGORY_APPLICATION_FAILURE: "Application Failure",
}


@dataclass(frozen=True, slots=True)
class _CauseMatch:
    category: str
    confidence: float
    evidence_texts: tuple[str, ...]


def _evidence_for(
    extracted: ExtractedEvidence,
    *,
    texts: tuple[str, ...],
) -> list[Evidence]:
    """Prefer extracted Evidence objects whose text matches; else synthesize."""
    by_text = {item.text: item for item in extracted.items}
    out: list[Evidence] = []
    for text in texts:
        existing = by_text.get(text)
        if existing is not None:
            out.append(existing)
        else:
            out.append(Evidence(source="other", text=text))
    return out


def _match_oom(extracted: ExtractedEvidence) -> _CauseMatch | None:
    if not extracted.has_oomkilled:
        return None
    preferred = (
        "OOMKilled event detected",
        "Container terminated with exit code 137",
        "Memory limit exceeded",
        "Container killed",
        "Exit code 137 indicates SIGKILL / OOMKilled",
    )
    texts = tuple(t for t in preferred if any(i.text == t for i in extracted.items))
    if not texts:
        texts = ("OOMKilled signal detected",)
    confidence = 0.95 if len(texts) >= 3 else 0.9
    return _CauseMatch(CATEGORY_OOMKILLED, confidence, texts)


def _match_invalid_configuration(extracted: ExtractedEvidence) -> _CauseMatch | None:
    if not extracted.has_invalid_configuration:
        return None
    preferred = (
        "Missing environment variable",
        "KeyError",
        "Configuration missing",
        "YAML parse error",
        "Invalid configuration",
        "Secret missing",
        "FailedMount",
    )
    texts = tuple(t for t in preferred if any(i.text == t for i in extracted.items))
    if not texts:
        texts = ("Configuration signal detected",)
    confidence = 0.9 if len(texts) >= 2 else 0.82
    return _CauseMatch(CATEGORY_INVALID_CONFIGURATION, confidence, texts)


def _match_application_failure(extracted: ExtractedEvidence) -> _CauseMatch | None:
    if not extracted.has_application_failure:
        return None
    preferred = (
        "Unhandled exception",
        "Traceback",
        "panic",
        "fatal error",
        "application exception",
    )
    texts = tuple(t for t in preferred if any(i.text == t for i in extracted.items))
    if not texts:
        texts = ("Application failure signal detected",)
    confidence = 0.88 if len(texts) >= 2 else 0.8
    return _CauseMatch(CATEGORY_APPLICATION_FAILURE, confidence, texts)


def _select_cause(extracted: ExtractedEvidence) -> _CauseMatch:
    """Priority: OOMKilled → Invalid Configuration → Application Failure."""
    for matcher in (_match_oom, _match_invalid_configuration, _match_application_failure):
        match = matcher(extracted)
        if match is not None:
            return match

    if extracted.has_generic_failure and any(code not in (0, 137) for code in extracted.exit_codes):
        return _CauseMatch(
            CATEGORY_APPLICATION_FAILURE,
            0.35,
            ("Non-zero exit code observed (low diagnostic weight)",),
        )
    return _CauseMatch(
        CATEGORY_APPLICATION_FAILURE,
        0.3,
        ("Insufficient specific evidence; generic crash signals only",),
    )


def diagnose_observations(observations: Observations) -> Diagnosis:
    """
    Observation → evidence extraction → deterministic Diagnosis.

    Scoped to three CrashLoopBackOff root causes:
    OOMKilled, Invalid Configuration, Application Failure.
    """
    extracted = extract_evidence(observations)
    match = _select_cause(extracted)
    return Diagnosis(
        summary=_SUMMARIES[match.category],
        category=match.category,
        confidence=match.confidence,
        evidence=_evidence_for(extracted, texts=match.evidence_texts),
    )
