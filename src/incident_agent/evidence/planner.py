"""Evidence Collection Planner: hypotheses → EvidenceRequests (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import EvidenceRequest, IncidentState


@dataclass(frozen=True, slots=True)
class _Need:
    type: str
    query: str
    rationale: str


# Required evidence per existing hypothesis cause (verification-driven curiosity).
_NEEDS_BY_CAUSE: dict[str, tuple[_Need, ...]] = {
    "Memory leak": (
        _Need(
            "metric",
            "container_memory_usage_bytes",
            "Memory trend / heap growth over time",
        ),
        _Need(
            "event",
            "restart_history",
            "Repeated OOM / CrashLoop restart history",
        ),
    ),
    "Memory limit too low": (
        _Need(
            "describe",
            "container_memory_limits",
            "Configured memory requests/limits vs peak usage",
        ),
        _Need(
            "metric",
            "container_memory_working_set_bytes",
            "Compare peak RSS to the configured limit",
        ),
    ),
    "Traffic spike": (
        _Need(
            "metric",
            "http_requests_per_second",
            "Request rate around the OOM window",
        ),
        _Need(
            "event",
            "hpa_events",
            "Autoscaler / load-related events",
        ),
    ),
    "Wrong image tag": (
        _Need("event", "image_pull_events", "ErrImagePull / manifest resolution events"),
        _Need("describe", "pod_container_statuses", "Container image reference and pull status"),
    ),
    "Image deleted or repository missing": (
        _Need("event", "image_pull_events", "Registry not-found / deleted image signals"),
    ),
    "Missing registry credentials": (
        _Need("describe", "image_pull_secrets", "imagePullSecrets / registry auth wiring"),
        _Need("event", "image_pull_events", "Unauthorized / pull access denied events"),
    ),
    "Secret not created": (
        _Need("describe", "secret_exists", "Confirm secret presence in namespace"),
        _Need("event", "failed_mount_events", "FailedMount secret events"),
    ),
    "Wrong secret name or namespace": (
        _Need("describe", "secret_references", "Compare secretRef names to existing secrets"),
    ),
    "Incorrect volume or envFrom mount": (
        _Need("describe", "volume_mounts", "Inspect volumeMounts / envFrom paths"),
    ),
    "Unhandled exception in application": (
        _Need("log", "previous_container_logs", "Stack trace from previous container instance"),
    ),
    "Bad configuration": (
        _Need("log", "previous_container_logs", "Config parse errors from previous logs"),
        _Need("describe", "configmap_refs", "ConfigMap / env value references"),
    ),
    "Missing environment variable": (
        _Need("describe", "pod_env", "Required env vars vs pod spec"),
        _Need("log", "previous_container_logs", "Missing-env messages in logs"),
    ),
    "Application crash on startup": (
        _Need("log", "previous_container_logs", "Startup exit reason and logs"),
        _Need("event", "restart_history", "Restart / BackOff history"),
    ),
    "Dependency unavailable": (
        _Need("log", "previous_container_logs", "Connection / DNS errors"),
        _Need("describe", "dependency_endpoints", "Dependent Service/Endpoints readiness"),
    ),
    "Misconfigured workload": (
        _Need("event", "failed_mount_events", "FailedMount / invalid reference events"),
        _Need("describe", "pod_events_summary", "Workload reference sanity check"),
    ),
}


def _target_ref(state: IncidentState) -> str:
    extra = state.observations.extra
    if isinstance(extra.get("target_ref"), str) and extra["target_ref"]:
        return str(extra["target_ref"])
    labels = state.alert.labels
    for key in ("service", "app", "workload", "deployment"):
        if labels.get(key):
            return str(labels[key])
    if state.resource and state.resource.name:
        kind = state.resource.kind or "workload"
        return f"{kind}/{state.resource.name}"
    return state.alert.alert_name or "<workload>"


def plan_evidence_requests(state: IncidentState) -> list[EvidenceRequest]:
    """
    Hypothesis → required evidence → EvidenceRequest list.

    Pure planning: no provider calls. Dedupes identical (type, query, target).
    """
    if not state.hypotheses:
        raise ValueError("state.hypotheses is required before plan_evidence_requests()")

    target = _target_ref(state)
    requests: list[EvidenceRequest] = []
    seen: set[str] = set()
    seq = 0

    for hyp in state.hypotheses:
        needs = _NEEDS_BY_CAUSE.get(hyp.description, ())
        if not needs and hyp.verification_checks:
            needs = (
                _Need("describe", "verification_probe", hyp.verification_checks[0]),
            )
        for need in needs:
            key = f"{need.type}|{need.query}|{target}"
            if key in seen:
                continue
            seen.add(key)
            seq += 1
            slug = hyp.hypothesis_id.replace(" ", "_")
            requests.append(
                EvidenceRequest(
                    request_id=f"er-{seq}-{slug}",
                    type=need.type,  # type: ignore[arg-type]
                    query=need.query,
                    target=target,
                    hypothesis_id=hyp.hypothesis_id,
                    rationale=need.rationale,
                )
            )

    return requests
