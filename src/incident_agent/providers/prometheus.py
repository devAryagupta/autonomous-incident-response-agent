"""Prometheus metrics provider: EvidenceRequest → PromQL → TelemetryResult / EvidenceResult.

Live HTTP stays behind BasePrometheusClient. Reasoning nodes never import this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from typing import Any

from incident_agent.contracts import EvidenceRequest, EvidenceResult, IncidentState
from incident_agent.evidence.models import (
    EvidenceRequest as LoopEvidenceRequest,
)
from incident_agent.evidence.models import (
    EvidenceType,
    TelemetryResult,
)
from incident_agent.providers.memory_series import (
    format_mi,
    near_limit,
    summarize_memory_usage,
)


class BasePrometheusClient(ABC):
    """Hexagonal port for PromQL range queries."""

    @abstractmethod
    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "5m",
    ) -> list[dict[str, Any]]:
        """
        Return time-series samples as ``[{"timestamp": datetime|float, "value": float}, ...]``.
        """
        ...


class FakePrometheusClient(BasePrometheusClient):
    """
    Deterministic stand-in for tests / offline demos.

    Keys may be short planner names (``container_memory_usage_bytes``) or
    substrings of full PromQL. Values are MiB (or RPS) float series.
    """

    def __init__(self, series_by_query: dict[str, list[float]] | None = None) -> None:
        self._series = dict(series_by_query or {})

    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "5m",
    ) -> list[dict[str, Any]]:
        _ = step
        values = self._lookup(query)
        if not values:
            return []
        return _series_to_samples(values, start=start, end=end)

    def _lookup(self, query: str) -> list[float]:
        for key, values in self._series.items():
            if key == query or key in query:
                return list(values)
        return []


# Backward-compatible alias used by earlier Bayesian-loop tests.
StaticPrometheusClient = FakePrometheusClient


class PrometheusClient(BasePrometheusClient):
    """Live Prometheus HTTP client (`/api/v1/query_range`) via httpx."""

    def __init__(self, *, base_url: str, timeout_s: float = 10.0) -> None:
        self._base_url = normalize_prometheus_url(base_url)
        self._timeout_s = timeout_s

    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "15s",
    ) -> list[dict[str, Any]]:
        import httpx

        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }
        try:
            with httpx.Client(timeout=self._timeout_s) as client:
                response = client.get(
                    f"{self._base_url}/api/v1/query_range",
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception:  # noqa: BLE001 — live Prom down must not abort the graph
            return []
        return _samples_from_prometheus_payload(payload)


def live_prometheus_client(*, base_url: str, timeout_s: float = 10.0) -> PrometheusClient:
    """Factory for live Prometheus HTTP access."""
    return PrometheusClient(base_url=base_url, timeout_s=timeout_s)


def normalize_prometheus_url(url: str) -> str:
    """Accept UI paths like ``/query``; the client talks to the API root."""
    raw = url.strip().rstrip("/")
    for suffix in ("/query", "/graph", "/classic/graph"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break
    return raw.rstrip("/")


def build_promql(
    query: str,
    target: str,
    *,
    namespace: str | None = None,
) -> str:
    """
    Translate planner query + target into standard PromQL.

    ``pod/oom-demo`` → pod="oom-demo"
    ``deployment/payment-service`` → pod=~"payment-service-.*"
    """
    selector = _label_selector(target, namespace=namespace)
    if query == "container_memory_usage_bytes":
        return f"container_memory_usage_bytes{{{selector}}}"
    if query == "container_memory_working_set_bytes":
        return f"max_over_time(container_memory_working_set_bytes{{{selector}}}[5m])"
    if query == "http_requests_per_second":
        workload = _workload_from_target(target)
        extra = f',namespace="{namespace}"' if namespace else ""
        return f'rate(http_requests_total{{service="{workload}"{extra}}}[5m])'
    return query


# Public alias matching earlier call sites / docs.
_promql_for = build_promql


def _workload_from_target(target: str) -> str:
    raw = target.strip()
    if "/" in raw:
        return raw.split("/")[-1]
    return raw


def _label_selector(target: str, *, namespace: str | None) -> str:
    raw = target.strip()
    kind = "pod"
    name = raw
    if "/" in raw:
        kind, name = raw.split("/", 1)
        kind = kind.lower()
    if kind == "pod":
        matcher = f'pod="{name}"'
    else:
        matcher = f'pod=~"{name}-.*"'
    if namespace:
        return f'namespace="{namespace}",{matcher}'
    return matcher


def _namespace_from_state(state: IncidentState) -> str | None:
    if state.resource is not None and state.resource.namespace:
        return state.resource.namespace
    extra_ns = state.observations.extra.get("namespace")
    if isinstance(extra_ns, str) and extra_ns.strip():
        return extra_ns.strip()
    labeled = state.alert.labels.get("namespace") or state.alert.labels.get("ns")
    return labeled or None


def _parse_time_range(time_range: str) -> timedelta:
    raw = time_range.strip().lower()
    if raw.endswith("m") and raw[:-1].isdigit():
        return timedelta(minutes=int(raw[:-1]))
    if raw.endswith("h") and raw[:-1].isdigit():
        return timedelta(hours=int(raw[:-1]))
    if raw.endswith("s") and raw[:-1].isdigit():
        return timedelta(seconds=int(raw[:-1]))
    return timedelta(minutes=30)


def _series_to_samples(
    values: list[float],
    *,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    if not values:
        return []
    if len(values) == 1:
        return [{"timestamp": start, "value": float(values[0])}]
    step = (end - start) / (len(values) - 1)
    return [
        {"timestamp": start + step * i, "value": float(v)}
        for i, v in enumerate(values)
    ]


def _samples_from_prometheus_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    results = data.get("result") or []
    chosen = _pick_range_result(results)
    if chosen is None:
        return []
    values = chosen.get("values") or []
    out: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            ts = float(item[0])
            value = float(item[1])
        except (TypeError, ValueError):
            continue
        out.append({"timestamp": datetime.fromtimestamp(ts, tz=UTC), "value": value})
    return out


def _pick_range_result(results: list[Any]) -> dict[str, Any] | None:
    """Skip pause/sandbox; prefer a series that moves, then the highest peak."""
    if not results:
        return None
    ranked: list[tuple[float, float, int, dict[str, Any]]] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        metric = row.get("metric") or {}
        image = str(metric.get("image") or "").lower()
        if "pause" in image:
            continue
        values = _series_values(row)
        if not values:
            continue
        peak = max(values)
        ranked.append((peak - min(values), peak, len(values), row))
    if not ranked:
        first = results[0]
        return first if isinstance(first, dict) else None
    ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return ranked[0][3]


def _series_values(row: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for item in row.get("values") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            values.append(float(item[1]))
        except (TypeError, ValueError):
            continue
    return values


def _datapoints_from_samples(samples: list[dict[str, Any]]) -> list[float]:
    out: list[float] = []
    for sample in samples:
        try:
            out.append(float(sample["value"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _timestamps_from_samples(samples: list[dict[str, Any]]) -> list[datetime] | None:
    stamps: list[datetime] = []
    for sample in samples:
        ts = sample.get("timestamp")
        if isinstance(ts, datetime):
            stamps.append(ts)
        elif isinstance(ts, (int, float)):
            stamps.append(datetime.fromtimestamp(float(ts), tz=UTC))
    return stamps or None


class PrometheusMetricsProvider:
    """
    MetricsProvider: contracts.EvidenceRequest (type=metric) → EvidenceResult,
    plus Bayesian-loop ``query_telemetry`` → TelemetryResult.
    """

    def __init__(self, client: BasePrometheusClient) -> None:
        self._client = client

    def fetch_metrics(self, state: IncidentState) -> dict[str, Any]:
        target = str(state.observations.extra.get("target_ref", state.alert.alert_name))
        namespace = _namespace_from_state(state)
        end = datetime.now(tz=UTC)
        start = end - timedelta(minutes=30)
        promql = build_promql(
            "container_memory_usage_bytes",
            target,
            namespace=namespace,
        )
        samples = self._client.query_range(promql, start, end, step="15s")
        series = _series_in_query_units(
            "container_memory_usage_bytes",
            _datapoints_from_samples(samples),
        )
        payload: dict[str, Any] = {
            "provider": "prometheus",
            "series": {"container_memory_usage_bytes": series},
            "target": target,
            "promql": promql,
        }
        if len(series) >= 2:
            payload["memory_mi"] = {
                "start": series[0],
                "end": max(series),
                "peak": max(series),
            }
        return payload

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        """Fulfill metric EvidenceRequest via PromQL range query."""
        if request.type != "metric":
            return EvidenceResult(
                request_id=request.request_id,
                type=request.type,
                query=request.query,
                target=request.target,
                success=False,
                summary=(
                    "PrometheusMetricsProvider only handles metric requests, "
                    f"got {request.type}"
                ),
                data={"provider": "prometheus", "error": "unsupported_type"},
            )

        namespace = _namespace_from_state(state)
        telemetry = self.query_telemetry(
            LoopEvidenceRequest(
                type=EvidenceType.METRIC,
                query=request.query,
                target=request.target,
                time_range="30m",
            ),
            namespace=namespace,
        )
        series = _series_in_query_units(request.query, telemetry.datapoints)
        promql = build_promql(request.query, request.target, namespace=namespace)
        data = _metric_result_data(
            request,
            series=series,
            promql=promql,
            memory_limit=state.observations.extra.get("memory_limit"),
        )
        summary = _summarize_series(
            request.query,
            request.target,
            series,
            memory_limit=state.observations.extra.get("memory_limit"),
        )
        return EvidenceResult(
            request_id=request.request_id,
            type=request.type,
            query=request.query,
            target=request.target,
            success=True,
            summary=summary,
            data=data,
        )

    def query_telemetry(
        self,
        request: LoopEvidenceRequest,
        *,
        namespace: str | None = None,
    ) -> TelemetryResult:
        """Bayesian-loop port: loop EvidenceRequest → TelemetryResult."""
        end = datetime.now(tz=UTC)
        start = end - _parse_time_range(request.time_range)
        promql = build_promql(request.query, request.target, namespace=namespace)
        samples = self._client.query_range(promql, start, end, step="15s")
        if not samples:
            samples = self._client.query_range(request.query, start, end, step="15s")
        return TelemetryResult(
            request=request,
            datapoints=_datapoints_from_samples(samples),
            timestamps=_timestamps_from_samples(samples),
        )


def _metric_result_data(
    request: EvidenceRequest,
    *,
    series: list[float],
    promql: str,
    memory_limit: object,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "provider": "prometheus",
        "query": request.query,
        "target": request.target,
        "promql": promql,
        "series": series,
    }
    if request.query == "container_memory_usage_bytes" and len(series) >= 2:
        data["memory_mi"] = {
            "start": series[0],
            "end": max(series),
            "peak": max(series),
        }
    if request.query == "container_memory_working_set_bytes" and series:
        data["working_set_mi"] = max(series)
        limit_mi = _parse_limit_mi(memory_limit)
        if limit_mi is not None:
            data["limit_mi"] = limit_mi
    return data


def _series_in_query_units(query: str, series: list[float]) -> list[float]:
    """cAdvisor ``*_bytes`` series are bytes; the fake client already stores Mi."""
    if not query.endswith("_bytes"):
        return series
    if not series or max(series) <= 4096:
        return series
    return [value / (1024 * 1024) for value in series]


def _parse_limit_mi(raw: object) -> float | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    if text.endswith("mi") and text[:-2].isdigit():
        return float(text[:-2])
    if text.endswith("gi") and text[:-2].isdigit():
        return float(text[:-2]) * 1024
    return None


def _summarize_series(
    query: str,
    target: str,
    series: list[float],
    *,
    memory_limit: object = None,
) -> str:
    if query in {
        "container_memory_usage_bytes",
        "container_memory_working_set_bytes",
    }:
        if len(series) < 2:
            return f"Insufficient memory samples for {target}"
        peak_mi = max(series)
        limit_mi = _parse_limit_mi(memory_limit)
        if query == "container_memory_working_set_bytes" and limit_mi is not None:
            label = format_mi(peak_mi)
            limit_label = format_mi(limit_mi)
            if near_limit(peak_mi, limit_mi):
                return f"Peak RSS {label} near memory limit {limit_label} for {target}"
            return f"Peak RSS {label} under limit {limit_label} for {target}"
        return summarize_memory_usage(series, limit_mi=limit_mi)
    if query == "http_requests_per_second":
        if not series:
            return f"No RPS samples for {target}"
        baseline, peak = min(series), max(series)
        if peak >= 3.0 * max(baseline, 1.0):
            return f"Traffic spike rps={peak} for {target}"
        return f"Request rate stable (~{baseline}-{peak} requests/sec) for {target}"
    return f"Prometheus metric result for query={query} target={target}"
