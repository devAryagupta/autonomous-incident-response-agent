"""Adjust hypothesis priors from retrieved historical episodes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from incident_agent.memory.models import MemoryRetrievalResult

# Historical remediation / confirmed cause → hypothesis candidate slug.
_CAUSE_TO_SLUG: dict[str, str] = {
    "memory leak": "memory_leak",
    "memory limit too low": "memory_limit_too_low",
    "traffic spike": "traffic_spike",
    "wrong image tag": "wrong_image_tag",
    "image deleted or repository missing": "image_deleted",
    "missing registry credentials": "missing_registry_auth",
    "secret not created": "secret_not_created",
    "wrong secret name or namespace": "wrong_secret_ref",
    "incorrect volume or envfrom mount": "incorrect_secret_mount",
    "unhandled exception in application": "unhandled_exception",
    "bad configuration": "bad_config",
    "missing environment variable": "missing_env_var",
}

_ACTION_TO_SLUG: dict[str, str] = {
    "increase_memory_limit": "memory_limit_too_low",
    "rollback_deployment": "unhandled_exception",
    "scale_deployment": "traffic_spike",
    "create_or_fix_secret": "secret_not_created",
    "fix_secret_reference": "wrong_secret_ref",
    "fix_volume_mount": "incorrect_secret_mount",
    "patch_image_tag": "wrong_image_tag",
    "create_image_pull_secret": "missing_registry_auth",
    "patch_config": "bad_config",
    "patch_env_var": "missing_env_var",
}

# Caps so memory cannot fully dominate fresh evidence.
_MAX_MEMORY_BOOST = 0.45
_MIN_SIMILARITY = 0.2


def _parse_retrievals(raw: list[Any]) -> list[MemoryRetrievalResult]:
    out: list[MemoryRetrievalResult] = []
    for item in raw:
        if isinstance(item, MemoryRetrievalResult):
            out.append(item)
        elif isinstance(item, dict):
            try:
                out.append(MemoryRetrievalResult.model_validate(item))
            except Exception:
                continue
    return out


def _slug_for_episode(confirmed: str, action: str) -> str | None:
    cause_key = confirmed.strip().lower()
    if cause_key in _CAUSE_TO_SLUG:
        return _CAUSE_TO_SLUG[cause_key]
    action_key = action.strip().lower()
    if action_key in _ACTION_TO_SLUG:
        return _ACTION_TO_SLUG[action_key]
    return None


def prior_adjustments_from_memory(
    *,
    candidate_slugs: Iterable[str],
    similar_incidents: list[Any],
) -> dict[str, float]:
    """
    Return additive prior boosts keyed by candidate slug.

    Only episodes with root_cause_verified count. Service recovery after a
    palliative restart is not evidence that the restart fixed the cause.
    """
    allowed = set(candidate_slugs)
    retrievals = [
        r
        for r in _parse_retrievals(similar_incidents)
        if r.similarity_score >= _MIN_SIMILARITY
    ]
    if not retrievals or not allowed:
        return {}

    weights: Counter[str] = Counter()
    total_weight = 0.0

    for result in retrievals:
        ep = result.episode
        if not ep.assessment.root_cause_verified:
            continue
        slug = _slug_for_episode(ep.confirmed_hypothesis, ep.remediation_action)
        if slug is None or slug not in allowed:
            continue
        w = float(result.similarity_score)
        weights[slug] += w
        total_weight += w

    if total_weight <= 0:
        return {}

    boosts: dict[str, float] = {}
    for slug, weight in weights.items():
        share = weight / total_weight
        boosts[slug] = round(min(_MAX_MEMORY_BOOST, share * _MAX_MEMORY_BOOST * 1.5), 4)
    return boosts
