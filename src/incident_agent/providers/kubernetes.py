"""Kubernetes observation provider: cluster signals → Observations contracts.

Live I/O stays here. Diagnosis / hypothesis / verification / remediation never
import this module — they only see Observations strings + extra metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from incident_agent.contracts import (
    EvidenceRequest,
    EvidenceResult,
    IncidentState,
    Observations,
    ResourceRef,
)

_TARGET_RE = re.compile(
    r"^(?:(?P<kind>pod|deployment|statefulset|daemonset|replicaset|service)/)?"
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)"
    r"(?:/(?P<container>[A-Za-z0-9][A-Za-z0-9_.-]*))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ContainerStatusSnapshot:
    name: str
    image: str | None = None
    ready: bool = False
    restart_count: int = 0
    state: str = "unknown"  # running | waiting | terminated | unknown
    reason: str | None = None
    exit_code: int | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PodSnapshot:
    """Normalized pod view — no kubernetes client types."""

    namespace: str
    name: str
    phase: str = "Unknown"
    ready: bool = False
    restart_count: int = 0
    image: str | None = None
    exit_code: int | None = None
    terminated_reason: str | None = None
    waiting_reason: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    containers: tuple[ContainerStatusSnapshot, ...] = ()
    image_pull_secrets: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    configmap_refs: tuple[str, ...] = ()
    volume_mounts: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)
    owner_kind: str | None = None
    owner_name: str | None = None


@dataclass(frozen=True, slots=True)
class EventRecord:
    type: str
    reason: str
    message: str
    count: int = 1
    involved_object: str = ""


@runtime_checkable
class KubernetesClient(Protocol):
    """Minimal cluster read surface used by KubernetesObservationProvider."""

    def get_pod(self, namespace: str, name: str) -> PodSnapshot | None:
        """Return a pod by name, or None if missing."""
        ...

    def find_pod_for_workload(
        self,
        namespace: str,
        *,
        kind: str | None,
        name: str,
    ) -> PodSnapshot | None:
        """Resolve deployment/statefulset/… (or bare name) to a representative pod."""
        ...

    def list_pod_events(self, namespace: str, pod_name: str) -> list[EventRecord]:
        """Events involving the pod."""
        ...

    def read_container_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 50,
    ) -> list[str]:
        """Container log lines (current or previous instance)."""
        ...

    def secret_exists(self, namespace: str, secret_name: str) -> bool:
        """True if the Secret exists in the namespace."""
        ...


@dataclass(frozen=True, slots=True)
class _ResolvedTarget:
    namespace: str
    kind: str
    name: str
    container: str | None = None


def resolve_k8s_target(state: IncidentState) -> _ResolvedTarget | None:
    """Derive namespace/kind/name from ResourceRef, target_ref, or alert labels."""
    resource = state.resource
    labels = state.alert.labels
    extra = state.observations.extra

    namespace = None
    kind = None
    name = None
    container = None

    if resource is not None:
        namespace = resource.namespace
        kind = (resource.kind or "pod").lower()
        name = resource.name

    target_ref = extra.get("target_ref")
    if isinstance(target_ref, str) and target_ref.strip():
        parsed = _parse_target_ref(target_ref.strip())
        if parsed is not None:
            kind = kind or parsed[0]
            name = name or parsed[1]
            container = container or parsed[2]

    namespace = namespace or labels.get("namespace") or labels.get("ns") or "default"
    if name is None:
        for key in ("pod", "pod_name", "service", "app", "workload", "deployment"):
            if labels.get(key):
                name = str(labels[key])
                if key == "deployment":
                    kind = kind or "deployment"
                elif key == "pod" or key == "pod_name":
                    kind = kind or "pod"
                break
    if name is None and state.alert.alert_name:
        # Last resort: alert name is rarely a pod name; skip if empty.
        pass

    if not name:
        return None

    kind = (kind or "pod").lower()
    return _ResolvedTarget(
        namespace=str(namespace),
        kind=kind,
        name=str(name),
        container=container,
    )


def _parse_target_ref(raw: str) -> tuple[str, str, str | None] | None:
    match = _TARGET_RE.match(raw.strip())
    if match is None:
        return None
    kind = (match.group("kind") or "pod").lower()
    name = match.group("name")
    container = match.group("container")
    return kind, name, container


def observations_from_pod(
    pod: PodSnapshot,
    *,
    events: list[EventRecord],
    log_lines: list[str],
    target_ref: str,
) -> Observations:
    """Translate live pod/events/logs into the string Observations contract."""
    logs = _normalize_log_lines(pod, log_lines)
    event_lines = _normalize_event_lines(pod, events)
    extra: dict[str, Any] = {
        "provider": "kubernetes",
        "target_ref": target_ref,
        "namespace": pod.namespace,
        "pod": pod.name,
        "phase": pod.phase,
        "ready": pod.ready,
        "restart_count": pod.restart_count,
        "image": pod.image,
        "exit_code": pod.exit_code,
        "terminated_reason": pod.terminated_reason,
        "waiting_reason": pod.waiting_reason,
        "memory_request": pod.memory_request,
        "memory_limit": pod.memory_limit,
        "image_pull_secrets": list(pod.image_pull_secrets),
        "secret_refs": list(pod.secret_refs),
        "configmap_refs": list(pod.configmap_refs),
        "volume_mounts": list(pod.volume_mounts),
        "env_names": list(pod.env_names),
        "labels": dict(pod.labels),
    }
    if pod.terminated_reason:
        extra["reason"] = pod.terminated_reason
        extra["last_state_reason"] = pod.terminated_reason
    elif pod.waiting_reason:
        extra["reason"] = pod.waiting_reason
    if pod.exit_code is not None:
        extra["container_exit_code"] = pod.exit_code
    return Observations(logs=logs, events=event_lines, extra=extra)


def _normalize_log_lines(pod: PodSnapshot, log_lines: list[str]) -> list[str]:
    logs: list[str] = []
    if pod.exit_code is not None:
        logs.append(f"Container exited with code {pod.exit_code}")
    if pod.terminated_reason:
        logs.append(
            f"Container terminated: reason={pod.terminated_reason} "
            f"exitCode={pod.exit_code if pod.exit_code is not None else 'unknown'}"
        )
    if pod.waiting_reason:
        logs.append(f"Container waiting: reason={pod.waiting_reason}")
    for line in log_lines:
        cleaned = line.rstrip("\n")
        if cleaned and cleaned not in logs:
            logs.append(cleaned)
    return logs


def _normalize_event_lines(pod: PodSnapshot, events: list[EventRecord]) -> list[str]:
    lines: list[str] = []
    for event in events:
        prefix = event.type or "Normal"
        reason = event.reason or "Unknown"
        message = event.message or ""
        count_suffix = f" (x{event.count})" if event.count > 1 else ""
        line = f"{prefix} {reason} {message}{count_suffix}".strip()
        lines.append(line)
        # Emit short reason tokens diagnosis already matches (OOMKilled, ErrImagePull, …).
        if reason and reason not in lines:
            lines.append(reason)

    if pod.terminated_reason and pod.terminated_reason not in lines:
        lines.append(pod.terminated_reason)
    if pod.waiting_reason and pod.waiting_reason not in lines:
        lines.append(pod.waiting_reason)
    if pod.restart_count >= 1 and not any("back-off" in line.lower() for line in lines):
        if pod.waiting_reason and "backoff" in (pod.waiting_reason or "").lower():
            lines.append(f"Back-off restarting failed container {pod.name}")
        elif pod.restart_count >= 2:
            lines.append(f"Back-off restarting failed container {pod.name}")
    return lines


class KubernetesObservationProvider:
    """
    ObservationProvider backed by a Kubernetes read client.

    Inject a FakeKubernetesClient in tests; use live_kubernetes_client() for clusters.
    Stage-0 defaults remain SyntheticObservationProvider — this is opt-in via bundle.
    """

    def __init__(self, client: KubernetesClient) -> None:
        self._client = client

    def fetch_observations(self, state: IncidentState) -> Observations:
        target = resolve_k8s_target(state)
        if target is None:
            # Keep any seed observations so offline/demo paths still work.
            return Observations(
                schema_version=state.observations.schema_version,
                logs=list(state.observations.logs),
                events=list(state.observations.events),
                extra={
                    **dict(state.observations.extra),
                    "provider": "kubernetes",
                    "error": "unable_to_resolve_target",
                },
            )

        pod = self._resolve_pod(target)
        if pod is None:
            return Observations(
                schema_version=state.observations.schema_version,
                logs=list(state.observations.logs),
                events=list(state.observations.events),
                extra={
                    **dict(state.observations.extra),
                    "provider": "kubernetes",
                    "error": "pod_not_found",
                    "target_ref": f"{target.kind}/{target.name}",
                    "namespace": target.namespace,
                },
            )

        events = self._client.list_pod_events(pod.namespace, pod.name)
        log_lines = self._client.read_container_logs(
            pod.namespace,
            pod.name,
            container=target.container,
            previous=bool(pod.exit_code or pod.terminated_reason),
            tail_lines=80,
        )
        target_ref = f"pod/{pod.name}" if target.kind == "pod" else f"{target.kind}/{target.name}"
        obs = observations_from_pod(
            pod,
            events=events,
            log_lines=log_lines,
            target_ref=target_ref,
        )
        # Preserve caller knobs (confidence_threshold, top_n, post_execution, …).
        merged_extra = dict(state.observations.extra)
        merged_extra.update(obs.extra)
        return Observations(
            schema_version=state.observations.schema_version,
            logs=obs.logs,
            events=obs.events,
            extra=merged_extra,
        )

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        query = request.query
        data: dict[str, Any] = {"provider": "kubernetes", "query": query}
        target = resolve_k8s_target(state)
        if target is None:
            return self._failed(request, data, f"Cannot resolve target for {query}")

        pod = self._resolve_pod(target)
        if pod is None:
            return self._failed(
                request,
                data,
                f"Pod not found for {target.kind}/{target.name} in {target.namespace}",
            )

        events = self._client.list_pod_events(pod.namespace, pod.name)
        summary = ""

        if query == "restart_history":
            data["restart_count"] = pod.restart_count
            summary = (
                f"Restart history for {request.target}: restart_count={pod.restart_count}"
            )
            if pod.restart_count == 0:
                summary += "; restart count decreases; no recent restarts"
            elif pod.restart_count >= 2:
                summary += "; repeated restarts observed"

        elif query == "failed_mount_events":
            failed = [
                e
                for e in events
                if e.reason.lower() == "failedmount"
                or ("secret" in e.message.lower() and "not found" in e.message.lower())
            ]
            data["secret_missing"] = bool(failed)
            if failed:
                summary = (
                    f"FailedMount events for {request.target}: {failed[0].message}"
                )
            else:
                summary = f"No FailedMount secret events for {request.target}"

        elif query == "image_pull_events":
            pull = [
                e
                for e in events
                if e.reason.lower() in {"errimagepull", "imagepullbackoff", "failed"}
                or "pull" in e.message.lower()
            ]
            waiting = (pod.waiting_reason or "").lower()
            if waiting in {"errimagepull", "imagepullbackoff"}:
                summary = f"Image pull events: {pod.waiting_reason} for {request.target}"
                data["waiting_reason"] = pod.waiting_reason
            elif pull:
                summary = f"Image pull events: {pull[0].reason} — {pull[0].message}"
            else:
                summary = f"No image pull failure events for {request.target}"

        elif query == "hpa_events":
            hpa = [
                e
                for e in events
                if "hpa" in e.reason.lower()
                or "scal" in e.message.lower()
                or "traffic" in e.message.lower()
            ]
            data["load_spike"] = bool(hpa)
            summary = (
                f"HPA/load events suggest traffic spike for {request.target}"
                if hpa
                else f"No HPA/load spike events for {request.target}"
            )

        elif query == "previous_container_logs":
            lines = self._client.read_container_logs(
                pod.namespace,
                pod.name,
                container=target.container,
                previous=True,
                tail_lines=40,
            )
            data["lines"] = lines[:10]
            summary = (
                "; ".join(lines[:5])
                if lines
                else f"No prior crash log lines available for {request.target}"
            )

        elif query == "secret_exists":
            refs = list(pod.secret_refs)
            if not refs:
                # Best-effort parse from FailedMount messages.
                for event in events:
                    m = re.search(
                        r'secret\s+[\"\']?([A-Za-z0-9_.-]+)',
                        event.message,
                        re.I,
                    )
                    if m:
                        refs.append(m.group(1))
            missing = [name for name in refs if not self._client.secret_exists(pod.namespace, name)]
            data["exists"] = not missing if refs else None
            data["secret_refs"] = refs
            data["missing"] = missing
            if missing:
                summary = f"Secret referenced by {request.target} does not exist"
            elif refs:
                summary = f"Secrets exist for {request.target}: {', '.join(refs)}"
            else:
                summary = f"Secret existence check inconclusive for {request.target}"

        elif query == "container_memory_limits":
            data["requests"] = pod.memory_request
            data["limits"] = pod.memory_limit
            summary = (
                f"Memory limits for {request.target}: "
                f"requests={pod.memory_request or 'unset'} "
                f"limits={pod.memory_limit or 'unset'}"
            )

        elif query == "pod_health":
            data["ready"] = pod.ready
            data["phase"] = pod.phase
            data["waiting_reason"] = pod.waiting_reason
            data["terminated_reason"] = pod.terminated_reason
            data["restart_count"] = pod.restart_count
            data["container_ready"] = all(c.ready for c in pod.containers) if pod.containers else pod.ready
            if pod.ready and pod.phase == "Running" and not pod.waiting_reason:
                summary = (
                    f"Pod health for {request.target}: Ready; pod becomes healthy; "
                    "no CrashLoopBackOff; crashloop clears"
                )
                if not any(
                    e.reason.lower() == "failedmount" for e in events
                ) and pod.secret_refs:
                    if all(self._client.secret_exists(pod.namespace, s) for s in pod.secret_refs):
                        summary += "; secret mount succeeds; secret exists"
                if not pod.waiting_reason or "image" not in (pod.waiting_reason or "").lower():
                    if pod.image:
                        summary += "; image pull succeeds; Pulled image"
            elif pod.waiting_reason and "crashloop" in pod.waiting_reason.lower():
                summary = (
                    f"Pod health for {request.target}: CrashLoopBackOff continues"
                )
            else:
                summary = (
                    f"Pod health for {request.target}: phase={pod.phase} "
                    f"ready={pod.ready} reason={pod.waiting_reason or pod.terminated_reason}"
                )

        elif query == "pod_container_statuses":
            data["containers"] = [
                {
                    "name": c.name,
                    "image": c.image,
                    "ready": c.ready,
                    "restart_count": c.restart_count,
                    "state": c.state,
                    "reason": c.reason,
                    "exit_code": c.exit_code,
                }
                for c in pod.containers
            ]
            if pod.containers:
                detail = "; ".join(
                    f"{c.name} image={c.image} ready={c.ready} restarts={c.restart_count}"
                    for c in pod.containers
                )
                summary = f"Container statuses for {request.target}: {detail}"
            else:
                summary = f"No container statuses for {request.target}"

        elif query == "image_pull_secrets":
            data["image_pull_secrets"] = list(pod.image_pull_secrets)
            summary = (
                f"imagePullSecrets for {request.target}: "
                f"{', '.join(pod.image_pull_secrets) or 'none'}"
            )

        elif query == "secret_references":
            data["secret_refs"] = list(pod.secret_refs)
            summary = (
                f"Secret refs for {request.target}: "
                f"{', '.join(pod.secret_refs) or 'none'}"
            )

        elif query == "volume_mounts":
            data["volume_mounts"] = list(pod.volume_mounts)
            summary = (
                f"Volume mounts for {request.target}: "
                f"{', '.join(pod.volume_mounts) or 'none'}"
            )

        elif query == "configmap_refs":
            data["configmap_refs"] = list(pod.configmap_refs)
            summary = (
                f"ConfigMap refs for {request.target}: "
                f"{', '.join(pod.configmap_refs) or 'none'}"
            )

        elif query == "pod_env":
            data["env_names"] = list(pod.env_names)
            summary = (
                f"Env vars for {request.target}: "
                f"{', '.join(pod.env_names) or 'none'}"
            )

        elif query == "pod_events_summary":
            data["event_count"] = len(events)
            summary = (
                "; ".join(f"{e.reason}: {e.message}" for e in events[:5])
                if events
                else f"No events for {request.target}"
            )

        elif query in {"dependency_endpoints", "verification_probe"}:
            summary = (
                f"Describe {query} for {request.target}: "
                f"pod={pod.name} phase={pod.phase} ready={pod.ready}"
            )
            data["phase"] = pod.phase
            data["ready"] = pod.ready

        else:
            summary = (
                f"Kubernetes {request.type} result for query={query} "
                f"target={request.target}"
            )

        return EvidenceResult(
            request_id=request.request_id,
            type=request.type,
            query=query,
            target=request.target,
            success=True,
            summary=summary,
            data=data,
        )

    def _resolve_pod(self, target: _ResolvedTarget) -> PodSnapshot | None:
        if target.kind == "pod":
            return self._client.get_pod(target.namespace, target.name)
        return self._client.find_pod_for_workload(
            target.namespace,
            kind=target.kind,
            name=target.name,
        )

    @staticmethod
    def _failed(
        request: EvidenceRequest,
        data: dict[str, Any],
        summary: str,
    ) -> EvidenceResult:
        data["error"] = summary
        return EvidenceResult(
            request_id=request.request_id,
            type=request.type,
            query=request.query,
            target=request.target,
            success=False,
            summary=summary,
            data=data,
        )


def live_kubernetes_client(*, context: str | None = None) -> KubernetesClient:
    """
    Build a client from local kubeconfig (optional dependency: kubernetes).

    Raises ImportError if the kubernetes package is not installed.
    """
    try:
        from kubernetes import client, config  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - optional dep
        raise ImportError(
            "Install the optional 'k8s' extra: pip install -e \".[k8s]\""
        ) from exc

    if context:
        config.load_kube_config(context=context)
    else:
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

    return _ApiKubernetesClient(
        core=client.CoreV1Api(),
        apps=client.AppsV1Api(),
    )


class _ApiKubernetesClient:
    """Adapter over kubernetes CoreV1/AppsV1 APIs → PodSnapshot / EventRecord."""

    def __init__(self, *, core: Any, apps: Any) -> None:
        self._core = core
        self._apps = apps

    def get_pod(self, namespace: str, name: str) -> PodSnapshot | None:
        try:
            pod = self._core.read_namespaced_pod(name=name, namespace=namespace)
        except Exception:  # noqa: BLE001 — map API 404/network to None
            return None
        return _pod_from_v1(pod)

    def find_pod_for_workload(
        self,
        namespace: str,
        *,
        kind: str | None,
        name: str,
    ) -> PodSnapshot | None:
        kind_l = (kind or "deployment").lower()
        label_selector = None
        try:
            if kind_l == "deployment":
                dep = self._apps.read_namespaced_deployment(name=name, namespace=namespace)
                label_selector = _selector_from_match_labels(
                    getattr(getattr(dep, "spec", None), "selector", None)
                )
            elif kind_l == "statefulset":
                sts = self._apps.read_namespaced_stateful_set(name=name, namespace=namespace)
                label_selector = _selector_from_match_labels(
                    getattr(getattr(sts, "spec", None), "selector", None)
                )
            elif kind_l == "daemonset":
                ds = self._apps.read_namespaced_daemon_set(name=name, namespace=namespace)
                label_selector = _selector_from_match_labels(
                    getattr(getattr(ds, "spec", None), "selector", None)
                )
            else:
                # Fall back: treat name as pod, then as app= label.
                direct = self.get_pod(namespace, name)
                if direct is not None:
                    return direct
                label_selector = f"app={name}"
        except Exception:  # noqa: BLE001
            label_selector = f"app={name}"

        try:
            pod_list = self._core.list_namespaced_pod(
                namespace=namespace,
                label_selector=label_selector,
            )
        except Exception:  # noqa: BLE001
            return None
        items = getattr(pod_list, "items", None) or []
        if not items:
            return None
        return _pod_from_v1(items[0])

    def list_pod_events(self, namespace: str, pod_name: str) -> list[EventRecord]:
        try:
            event_list = self._core.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={pod_name},involvedObject.kind=Pod",
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[EventRecord] = []
        for event in getattr(event_list, "items", None) or []:
            out.append(
                EventRecord(
                    type=str(getattr(event, "type", "") or "Normal"),
                    reason=str(getattr(event, "reason", "") or ""),
                    message=str(getattr(event, "message", "") or ""),
                    count=int(getattr(event, "count", 1) or 1),
                    involved_object=pod_name,
                )
            )
        return out

    def read_container_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None = None,
        previous: bool = False,
        tail_lines: int = 50,
    ) -> list[str]:
        try:
            raw = self._core.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                previous=previous,
                tail_lines=tail_lines,
                timestamps=False,
            )
        except Exception:  # noqa: BLE001
            return []
        return _split_log_payload(raw)

    def secret_exists(self, namespace: str, secret_name: str) -> bool:
        try:
            self._core.read_namespaced_secret(name=secret_name, namespace=namespace)
            return True
        except Exception:  # noqa: BLE001
            return False


def _split_log_payload(raw: Any) -> list[str]:
    """Decode kube API log payloads (str or bytes) into non-empty lines."""
    if raw is None:
        return []
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8", errors="replace")
    else:
        text = str(raw)
    return [line for line in text.splitlines() if line.strip()]


def _selector_from_match_labels(selector: Any) -> str | None:
    match_labels = getattr(selector, "match_labels", None) or {}
    if not match_labels:
        return None
    return ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))


def _pod_from_v1(pod: Any) -> PodSnapshot:
    meta = getattr(pod, "metadata", None)
    spec = getattr(pod, "spec", None)
    status = getattr(pod, "status", None)
    namespace = str(getattr(meta, "namespace", "") or "default")
    name = str(getattr(meta, "name", "") or "")
    labels = dict(getattr(meta, "labels", None) or {})
    phase = str(getattr(status, "phase", None) or "Unknown")

    containers: list[ContainerStatusSnapshot] = []
    restart_count = 0
    image: str | None = None
    exit_code: int | None = None
    terminated_reason: str | None = None
    waiting_reason: str | None = None
    ready = False

    for cs in getattr(status, "container_statuses", None) or []:
        c_name = str(getattr(cs, "name", "") or "")
        c_image = getattr(cs, "image", None)
        c_ready = bool(getattr(cs, "ready", False))
        c_restarts = int(getattr(cs, "restart_count", 0) or 0)
        restart_count = max(restart_count, c_restarts)
        image = image or (str(c_image) if c_image else None)
        ready = ready or c_ready

        state_obj = getattr(cs, "state", None)
        last_state = getattr(cs, "last_state", None)
        c_state = "unknown"
        c_reason = None
        c_exit = None
        c_message = None

        waiting = getattr(state_obj, "waiting", None) if state_obj else None
        terminated = getattr(state_obj, "terminated", None) if state_obj else None
        running = getattr(state_obj, "running", None) if state_obj else None
        last_term = getattr(last_state, "terminated", None) if last_state else None

        if waiting is not None:
            c_state = "waiting"
            c_reason = getattr(waiting, "reason", None)
            c_message = getattr(waiting, "message", None)
            waiting_reason = waiting_reason or (str(c_reason) if c_reason else None)
        elif terminated is not None:
            c_state = "terminated"
            c_reason = getattr(terminated, "reason", None)
            c_exit = getattr(terminated, "exit_code", None)
            c_message = getattr(terminated, "message", None)
            terminated_reason = terminated_reason or (str(c_reason) if c_reason else None)
            if c_exit is not None:
                exit_code = int(c_exit)
        elif running is not None:
            c_state = "running"

        # CrashLoopBackOff spends most of its time Waiting; OOMKilled lives on lastState.
        if last_term is not None:
            last_reason = getattr(last_term, "reason", None)
            last_exit = getattr(last_term, "exit_code", None)
            if terminated_reason is None and last_reason:
                terminated_reason = str(last_reason)
            if exit_code is None and last_exit is not None:
                exit_code = int(last_exit)
            if c_exit is None and last_exit is not None:
                c_exit = last_exit

        containers.append(
            ContainerStatusSnapshot(
                name=c_name,
                image=str(c_image) if c_image else None,
                ready=c_ready,
                restart_count=c_restarts,
                state=c_state,
                reason=str(c_reason) if c_reason else None,
                exit_code=int(c_exit) if c_exit is not None else None,
                message=str(c_message) if c_message else None,
            )
        )

    memory_request = None
    memory_limit = None
    env_names: list[str] = []
    volume_mounts: list[str] = []
    secret_refs: list[str] = []
    configmap_refs: list[str] = []

    for container in getattr(spec, "containers", None) or []:
        resources = getattr(container, "resources", None)
        requests = getattr(resources, "requests", None) or {}
        limits = getattr(resources, "limits", None) or {}
        if memory_request is None and requests.get("memory"):
            memory_request = str(requests["memory"])
        if memory_limit is None and limits.get("memory"):
            memory_limit = str(limits["memory"])
        for env in getattr(container, "env", None) or []:
            if getattr(env, "name", None):
                env_names.append(str(env.name))
            value_from = getattr(env, "value_from", None)
            if value_from is not None:
                secret_key = getattr(value_from, "secret_key_ref", None)
                if secret_key is not None and getattr(secret_key, "name", None):
                    secret_refs.append(str(secret_key.name))
                cm_key = getattr(value_from, "config_map_key_ref", None)
                if cm_key is not None and getattr(cm_key, "name", None):
                    configmap_refs.append(str(cm_key.name))
        for env_from in getattr(container, "env_from", None) or []:
            secret_ref = getattr(env_from, "secret_ref", None)
            if secret_ref is not None and getattr(secret_ref, "name", None):
                secret_refs.append(str(secret_ref.name))
            cm_ref = getattr(env_from, "config_map_ref", None)
            if cm_ref is not None and getattr(cm_ref, "name", None):
                configmap_refs.append(str(cm_ref.name))
        for mount in getattr(container, "volume_mounts", None) or []:
            m_name = getattr(mount, "name", None)
            m_path = getattr(mount, "mount_path", None)
            if m_name or m_path:
                volume_mounts.append(f"{m_name}:{m_path}")

    for volume in getattr(spec, "volumes", None) or []:
        secret = getattr(volume, "secret", None)
        if secret is not None and getattr(secret, "secret_name", None):
            secret_refs.append(str(secret.secret_name))
        cm = getattr(volume, "config_map", None)
        if cm is not None and getattr(cm, "name", None):
            configmap_refs.append(str(cm.name))

    image_pull_secrets = tuple(
        str(getattr(item, "name", ""))
        for item in (getattr(spec, "image_pull_secrets", None) or [])
        if getattr(item, "name", None)
    )

    owner_kind = None
    owner_name = None
    owners = getattr(meta, "owner_references", None) or []
    if owners:
        owner_kind = str(getattr(owners[0], "kind", None) or "") or None
        owner_name = str(getattr(owners[0], "name", None) or "") or None

    return PodSnapshot(
        namespace=namespace,
        name=name,
        phase=phase,
        ready=ready and phase == "Running",
        restart_count=restart_count,
        image=image,
        exit_code=exit_code,
        terminated_reason=terminated_reason,
        waiting_reason=waiting_reason,
        memory_request=memory_request,
        memory_limit=memory_limit,
        containers=tuple(containers),
        image_pull_secrets=image_pull_secrets,
        secret_refs=tuple(dict.fromkeys(secret_refs)),
        configmap_refs=tuple(dict.fromkeys(configmap_refs)),
        volume_mounts=tuple(volume_mounts),
        env_names=tuple(dict.fromkeys(env_names)),
        labels=labels,
        owner_kind=owner_kind,
        owner_name=owner_name,
    )


def resource_ref_from_pod(pod: PodSnapshot, *, cluster: str | None = None) -> ResourceRef:
    """Helper for ingest paths that want a typed ResourceRef on IncidentState."""
    return ResourceRef(
        system="kubernetes",
        namespace=pod.namespace,
        kind="Pod",
        name=pod.name,
        cluster=cluster,
    )
