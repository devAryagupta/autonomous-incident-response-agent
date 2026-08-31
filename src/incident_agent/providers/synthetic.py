"""Synthetic / Stage-0 provider implementations (no live infrastructure)."""

from __future__ import annotations

from typing import Any

from incident_agent.contracts import (
    EvidenceRequest,
    EvidenceResult,
    IncidentState,
    Observations,
)


def _joined_signals(state: IncidentState) -> str:
    parts = list(state.observations.logs) + list(state.observations.events)
    if state.diagnosis is not None:
        parts.append(state.diagnosis.category)
        parts.append(state.diagnosis.summary)
        parts.extend(e.text for e in state.diagnosis.evidence)
    return "\n".join(parts).lower()


class SyntheticObservationProvider:
    """
    Pass-through observations from IncidentState.

    Stage-0: dataset / fixture already populated `state.observations`.
    Live path: KubernetesObservationProvider (see providers.kubernetes).
    """

    def fetch_observations(self, state: IncidentState) -> Observations:
        return Observations(
            schema_version=state.observations.schema_version,
            logs=list(state.observations.logs),
            events=list(state.observations.events),
            extra=dict(state.observations.extra),
        )

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        """Fulfill log/event/describe curiosity requests deterministically."""
        signals = _joined_signals(state)
        query = request.query
        data: dict[str, Any] = {"provider": "synthetic", "query": query}
        summary = ""

        if query == "restart_history":
            # After a successful remediation dry-run, simulate recovery signals.
            if state.observations.extra.get("post_execution") and state.execution is not None:
                if state.execution.success:
                    data["restart_count"] = 0
                    summary = (
                        f"Restart history for {request.target}: restart_count=0; "
                        "restart count decreases; no recent restarts"
                    )
                else:
                    data["restart_count"] = 3
                    summary = (
                        f"Restart history for {request.target}: restart_count=3 "
                        "(execution failed; CrashLoop continues)"
                    )
            else:
                count = signals.count("backoff") + signals.count("oomkilled")
                count = max(count, 1 if "crashloop" in signals else 0)
                data["restart_count"] = count
                summary = (
                    f"Restart history for {request.target}: restart_count={count} "
                    f"(Back-off / OOM signals)"
                )
                if count >= 2:
                    summary += "; repeated restarts observed"

        elif query == "failed_mount_events":
            if "secret" in signals and "not found" in signals:
                summary = (
                    f"FailedMount events for {request.target}: "
                    "secret not found"
                )
                data["secret_missing"] = True
            else:
                summary = f"No FailedMount secret events for {request.target}"
                data["secret_missing"] = False

        elif query == "image_pull_events":
            if "manifest unknown" in signals:
                summary = f"Image pull events: manifest unknown for {request.target}"
            elif "unauthorized" in signals or "authentication required" in signals:
                summary = f"Image pull events: authentication required for {request.target}"
            elif "errimagepull" in signals or "imagepullbackoff" in signals:
                summary = f"Image pull events: ErrImagePull/ImagePullBackOff for {request.target}"
            else:
                summary = f"No image pull failure events for {request.target}"

        elif query == "hpa_events":
            if any(token in signals for token in ("traffic", "spike", "rps", "qps")):
                summary = f"HPA/load events suggest traffic spike for {request.target}"
                data["load_spike"] = True
            else:
                summary = f"No HPA/load spike events for {request.target}"
                data["load_spike"] = False

        elif query == "previous_container_logs":
            # Echo useful existing log lines as "collected" previous logs.
            interesting = [
                line
                for line in state.observations.logs
                if any(
                    token in line.lower()
                    for token in (
                        "traceback",
                        "exception",
                        "panic",
                        "fatal",
                        "config",
                        "environment",
                        "secret",
                    )
                )
            ][:5]
            data["lines"] = interesting
            summary = (
                "; ".join(interesting)
                if interesting
                else f"No prior crash log lines available for {request.target}"
            )

        elif query == "secret_exists":
            missing = "secret" in signals and "not found" in signals
            data["exists"] = not missing
            summary = (
                f"Secret referenced by {request.target} does not exist"
                if missing
                else f"Secret existence check inconclusive for {request.target}"
            )

        elif query == "container_memory_limits":
            summary = (
                f"Memory limits for {request.target}: requests=128Mi limits=256Mi "
                "(synthetic describe)"
            )
            data["requests_mi"] = 128
            data["limits_mi"] = 256

        elif query == "pod_health":
            if state.observations.extra.get("post_execution") and state.execution is not None:
                if state.execution.success:
                    summary = (
                        f"Pod health for {request.target}: Ready; pod becomes healthy; "
                        "no CrashLoopBackOff; crashloop clears"
                    )
                    data["ready"] = True
                    action = state.execution.action or ""
                    if "secret" in action:
                        summary += "; secret mount succeeds; secret exists"
                    if "image" in action or "pull" in action:
                        summary += "; image pull succeeds; Pulled image"
                else:
                    summary = (
                        f"Pod health for {request.target}: CrashLoopBackOff continues"
                    )
                    data["ready"] = False
            else:
                summary = f"Pod health for {request.target}: unknown (pre-execution)"
                data["ready"] = False

        elif query == "deployment_rollout_history":
            # Stage-0 synthetic fixtures usually do not include rollout timeline context.
            summary = f"Deployment rollout history unavailable for {request.target}"
            data["available"] = False

        elif query in {
            "pod_container_statuses",
            "image_pull_secrets",
            "secret_references",
            "volume_mounts",
            "configmap_refs",
            "pod_env",
            "dependency_endpoints",
            "pod_events_summary",
            "verification_probe",
        }:
            summary = f"Describe {query} for {request.target} (synthetic stub)"
            data["stub"] = True

        else:
            summary = f"Synthetic {request.type} result for query={query} target={request.target}"

        return EvidenceResult(
            request_id=request.request_id,
            type=request.type,
            query=query,
            target=request.target,
            success=True,
            summary=summary,
            data=data,
        )


class SyntheticMetricsProvider:
    """
    Deterministic metrics stub + evidence-request fulfillment.

    Later: PrometheusMetricsProvider will query PromQL (see providers.prometheus).
    """

    def fetch_metrics(self, state: IncidentState) -> dict[str, Any]:
        _ = state
        return {
            "provider": "synthetic",
            "series": {},
            "notes": "No live metrics in Stage 0",
        }

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        """Fulfill metric curiosity requests with deterministic synthetic series."""
        signals = _joined_signals(state)
        query = request.query
        data: dict[str, Any] = {"provider": "synthetic", "query": query, "target": request.target}
        summary = ""

        if query == "container_memory_usage_bytes":
            # OOM + restart/backoff → growing memory trend (leak-shaped).
            if "oom" in signals or "137" in signals:
                start_mi, end_mi = 200, 900
                if "startup" in signals and "backoff" not in signals:
                    start_mi, end_mi = 180, 256
                data["memory_mi"] = {"start": start_mi, "end": end_mi, "peak": end_mi}
                data["series"] = [start_mi, (start_mi + end_mi) // 2, end_mi]
                summary = f"Memory increased from {start_mi}Mi to {end_mi}Mi"
            else:
                data["memory_mi"] = {"start": 120, "end": 130, "peak": 130}
                summary = "Memory usage stable (~120-130Mi)"

        elif query == "container_memory_working_set_bytes":
            limit_mi = 256
            peak_mi = 250 if ("oom" in signals or "137" in signals) else 140
            data["working_set_mi"] = peak_mi
            data["limit_mi"] = limit_mi
            data["memory_mi"] = {"start": peak_mi - 20, "end": peak_mi, "peak": peak_mi}
            if peak_mi >= limit_mi * 0.9:
                summary = (
                    f"Peak RSS {peak_mi}Mi near memory limit {limit_mi}Mi "
                    f"for {request.target}"
                )
            else:
                summary = f"Peak RSS {peak_mi}Mi under limit {limit_mi}Mi for {request.target}"

        elif query == "http_requests_per_second":
            if any(token in signals for token in ("traffic", "spike", "rps", "qps")):
                data["rps"] = {"baseline": 50, "peak": 5000}
                summary = f"Traffic spike rps=5000 for {request.target}"
            else:
                data["rps"] = {"baseline": 40, "peak": 55}
                # Avoid bare "rps"/"idle traffic" tokens that falsely contradict other hyps.
                summary = (
                    f"Request rate stable (~40-55 requests/sec) for {request.target}"
                )

        else:
            summary = f"Synthetic metric stub for query={query} target={request.target}"
            data["series"] = {}

        return EvidenceResult(
            request_id=request.request_id,
            type=request.type,
            query=query,
            target=request.target,
            success=True,
            summary=summary,
            data=data,
        )
