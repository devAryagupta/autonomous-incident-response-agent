"""Evidence Collection Planner: hypotheses → EvidenceRequests (no I/O)."""

from __future__ import annotations

from dataclasses import dataclass

from incident_agent.contracts import EvidenceRequest, IncidentState


@dataclass(frozen=True, slots=True)

class _Need:
    type: str # type means the type of the evidence request. which provider to use to get the evidence.
    query: str # query means the query to the evidence request. command to run to get the evidence.
    rationale: str # rationale means the rationale for the evidence request. why we need this evidence.


# Required evidence per existing hypothesis cause (verification-driven curiosity).
_NEEDS_BY_CAUSE: dict[str, tuple[_Need, ...]] = {
    # Hypothesis 1: Memory leak :- Memory trend /heap growth over time and repeated OOM / CrashLoop restart history . in case of memory leak, we need to check the memory trend and the restart history.

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
    # Hypothesis 2: Memory limit too low :- Configured memory requests/limits vs peak usage and Compare peak RSS to the configured limit. in case of memory limit too low, we need to check the memory requests/limits and the peak usage.
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
    # Hypothesis 3: Traffic spike :- Request rate around the OOM window and Autoscaler / load-related events. in case of traffic spike, we need to check the request rate and the load-related events. hpa events are the events from the autoscaler
    "Traffic spike": (
        _Need(
            "metric",
            "http_requests_per_second",
            "Request rate around the OOM window",
        ),
        _Need(
            "event",
            "hpa_events", # horizontal pod autoscaler events are the events from the autoscaler.
            "Autoscaler / load-related events",
        ),
    ),
    # Hypothesis 4: Wrong image tag :- ErrImagePull / manifest resolution events and Container image reference and pull status. in case of wrong image tag, we need to check the image pull events and the container image reference and pull status.
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
    "Fatal runtime error": (
        _Need("log", "previous_container_logs", "Panic/fatal stack output from previous container"),
    ),
    "Startup regression after deploy": (
        _Need(
            "describe",
            "deployment_rollout_history",
            "Compare recent rollout revision/image to crash onset",
        ),
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
    target_ref = extra.get("target_ref")
    if isinstance(target_ref, str) and target_ref:
        return target_ref

    labels = state.alert.labels
    for key in ("service", "app", "workload", "deployment"):
        value = labels.get(key)
        if value:
            return str(value)

    resource = state.resource
    if resource and resource.name:
        kind = resource.kind or "workload"
        return f"{kind}/{resource.name}"

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
