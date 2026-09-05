"""Frozen diagnosis scope vs later evidence. Not a second diagnose pass."""

from __future__ import annotations

from incident_agent.contracts import Diagnosis, Observations
from incident_agent.diagnosis.evidence import ExtractedEvidence, extract_evidence

# Same families the hypothesis catalog is keyed by.
_SCOPE_FAMILY: dict[str, str] = {
    "OOMKilled": "oom",
    "Invalid Configuration": "config",
    "Missing Secret": "config",
    "Invalid Image": "config",
    "Application Failure": "app",
    "Application Crash": "app",
    "Unknown": "app",
}

_FAMILY_LABEL: dict[str, str] = {
    "oom": "OOMKilled",
    "config": "Invalid Configuration",
    "app": "Application Failure",
}


def diagnosis_scope_family(category: str) -> str:
    return _SCOPE_FAMILY.get(category, "app")


def _family_supported(family: str, extracted: ExtractedEvidence) -> bool:
    if family == "oom":
        return extracted.has_oomkilled
    if family == "config":
        return extracted.has_invalid_configuration
    return extracted.has_application_failure


def assess_diagnosis_scope(
    diagnosis: Diagnosis,
    observations: Observations,
) -> Diagnosis:
    """
    Keep the category frozen. Invalidate only on a strong scope contradiction:

    current evidence no longer supports the frozen family, and it does
    support a different family. Mixed signals stay in the original scope.
    """
    extracted = extract_evidence(observations)
    frozen = diagnosis_scope_family(diagnosis.category)
    if _family_supported(frozen, extracted):
        return diagnosis.model_copy(update={"scope_valid": True, "scope_invalid_reason": ""})

    rivals = [
        family
        for family in ("oom", "config", "app")
        if family != frozen and _family_supported(family, extracted)
    ]
    if not rivals:
        return diagnosis.model_copy(update={"scope_valid": True, "scope_invalid_reason": ""})

    rival_label = _FAMILY_LABEL[rivals[0]]
    reason = (
        f"Frozen scope {diagnosis.category} is unsupported by current evidence; "
        f"signals support {rival_label}"
    )
    return diagnosis.model_copy(update={"scope_valid": False, "scope_invalid_reason": reason})
