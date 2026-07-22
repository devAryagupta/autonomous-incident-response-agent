"""Deterministic diagnosis engine: observations → evidence → Diagnosis."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import Diagnosis, Evidence, Observations
from incident_agent.diagnosis.evidence import ExtractedEvidence, extract_evidence

CATEGORY_OOMKILLED = "OOMKilled"
CATEGORY_INVALID_IMAGE = "Invalid Image"
CATEGORY_MISSING_SECRET = "Missing Secret"
CATEGORY_APPLICATION_CRASH = "Application Crash"
CATEGORY_UNKNOWN = "Unknown"

_SUMMARIES: dict[str, str] = {
    CATEGORY_OOMKILLED: "Resource Constraint (OOMKilled)",
    CATEGORY_INVALID_IMAGE: "Invalid Image Tag / Image Pull Error",
    CATEGORY_MISSING_SECRET: "Missing Secret",
    CATEGORY_APPLICATION_CRASH: "Application Bug / Unhandled Exception",
    CATEGORY_UNKNOWN: "Insufficient evidence for a confident diagnosis",
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
    texts: list[str] = []
    if any(i.text.startswith("Container terminated with exit code 137") for i in extracted.items):
        texts.append("Container terminated with exit code 137")
    if any("OOMKilled" in i.text for i in extracted.items):
        texts.append("OOMKilled event detected")
    if not texts:
        texts = ["OOMKilled signal detected"]
    confidence = 0.9 if len(texts) >= 2 else 0.85
    return _CauseMatch(CATEGORY_OOMKILLED, confidence, tuple(texts))


def _match_invalid_image(extracted: ExtractedEvidence) -> _CauseMatch | None:
    if not extracted.has_image_pull_failure:
        return None
    preferred = (
        "ErrImagePull event detected",
        "ImagePullBackOff event detected",
        "Image manifest unknown",
        "Image pull failure detected",
    )
    texts = tuple(t for t in preferred if any(i.text == t for i in extracted.items))
    if not texts:
        texts = ("Image pull failure detected",)
    confidence = 0.9 if len(texts) >= 2 else 0.85
    return _CauseMatch(CATEGORY_INVALID_IMAGE, confidence, texts)


def _match_missing_secret(extracted: ExtractedEvidence) -> _CauseMatch | None:
    if not extracted.has_missing_secret:
        return None
    return _CauseMatch(
        CATEGORY_MISSING_SECRET,
        0.85,
        ("Missing secret / FailedMount secret detected",),
    )


def _match_application_crash(extracted: ExtractedEvidence) -> _CauseMatch | None:
    if not extracted.has_application_crash:
        return None
    texts: list[str] = []
    for item in extracted.items:
        if item.text in {
            "Application traceback detected",
            "Application panic detected",
            "Unhandled exception detected",
            "Application crash pattern detected",
        }:
            texts.append(item.text)
    for item in extracted.items:
        if item.text.startswith("Container terminated with exit code"):
            texts.append(item.text)
            break
    if not texts:
        texts = ["Application crash signal detected"]
    # Stronger when we have stack/panic evidence vs exit-code-only.
    has_stack = any("traceback" in t.lower() or "panic" in t.lower() or "exception" in t.lower() for t in texts)
    confidence = 0.8 if has_stack else 0.7
    return _CauseMatch(CATEGORY_APPLICATION_CRASH, confidence, tuple(texts[:3]))


def _select_cause(extracted: ExtractedEvidence) -> _CauseMatch:
    """Priority: OOM → Invalid Image → Missing Secret → Application Crash → Unknown."""
    for matcher in (
        _match_oom,
        _match_invalid_image,
        _match_missing_secret,
        _match_application_crash,
    ):
        match = matcher(extracted)
        if match is not None:
            return match
    return _CauseMatch(
        CATEGORY_UNKNOWN,
        0.3,
        ("No matching CrashLoopBackOff failure pattern in logs/events",),
    )


def diagnose_observations(observations: Observations) -> Diagnosis:
    """
    Observation → evidence extraction → deterministic Diagnosis.

    Supports four CrashLoopBackOff causes only: OOMKilled, Invalid Image,
    Missing Secret, Application Crash. No LLM / Kubernetes / Prometheus.
    """
    extracted = extract_evidence(observations)
    match = _select_cause(extracted)
    return Diagnosis(
        summary=_SUMMARIES[match.category],
        category=match.category,
        confidence=match.confidence,
        evidence=_evidence_for(extracted, texts=match.evidence_texts),
    )
