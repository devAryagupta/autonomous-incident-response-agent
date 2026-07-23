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
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s

    def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "5m",
    ) -> list[dict[str, Any]]:
        import httpx

        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }
        with httpx.Client(timeout=self._timeout_s) as client:
            response = client.get(f"{self._base_url}/api/v1/query_range", params=params)
            response.raise_for_status()
            payload = response.json()
        return _samples_from_prometheus_payload(payload)


def live_prometheus_client(*, base_url: str, timeout_s: float = 10.0) -> PrometheusClient:
    """Factory for live Prometheus HTTP access."""
    return PrometheusClient(base_url=base_url, timeout_s=timeout_s)


def build_promql(query: str, target: str) -> str:
    """
    Translate planner query + target into standard PromQL.

    ``deployment/payment-service`` → pod=~"payment-service-.*"
    """
    workload = _workload_from_target(target)
    if query == "container_memory_usage_bytes":
        return f'container_memory_usage_bytes{{pod=~"{workload}-.*"}}'
    if query == "container_memory_working_set_bytes":
        return f'container_memory_working_set_bytes{{pod=~"{workload}-.*"}}'
    if query == "http_requests_per_second":
        return f'rate(http_requests_total{{service="{workload}"}}[5m])'
    return query


# Public alias matching earlier call sites / docs.
_promql_for = build_promql


def _workload_from_target(target: str) -> str:
    raw = target.strip()
    if "/" in raw:
        return raw.split("/")[-1]
    return raw


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
    if not results:
        return []
    values = results[0].get("values") or []
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
        end = datetime.now(tz=UTC)
        start = end - timedelta(minutes=30)
        promql = build_promql("container_memory_usage_bytes", target)
        samples = self._client.query_range(promql, start, end)
        series = _datapoints_from_samples(samples)
        return {
            "provider": "prometheus",
            "series": {"container_memory_usage_bytes": series},
            "target": target,
            "promql": promql,
        }

    def execute_evidence_request(
        self,
        request: EvidenceRequest,
        *,
        state: IncidentState,
    ) -> EvidenceResult:
        """Fulfill metric EvidenceRequest via PromQL range query."""
        _ = state
        if request.type != "metric":
            return EvidenceResult(
                request_id=request.request_id,
                type=request.type,
                query=request.query,
                target=request.target,
                success=False,
                summary=f"PrometheusMetricsProvider only handles metric requests, got {request.type}",
                data={"provider": "prometheus", "error": "unsupported_type"},
            )

        telemetry = self.query_telemetry(
            LoopEvidenceRequest(
                type=EvidenceType.METRIC,
                query=request.query,
                target=request.target,
                time_range="30m",
            )
        )
        series = telemetry.datapoints
        promql = build_promql(request.query, request.target)
        data: dict[str, Any] = {
            "provider": "prometheus",
            "query": request.query,
            "target": request.target,
            "promql": promql,
            "series": series,
        }
        summary = _summarize_series(request.query, request.target, series)
        return EvidenceResult(
            request_id=request.request_id,
            type=request.type,
            query=request.query,
            target=request.target,
            success=True,
            summary=summary,
            data=data,
        )

    def query_telemetry(self, request: LoopEvidenceRequest) -> TelemetryResult:
        """Bayesian-loop port: loop EvidenceRequest → TelemetryResult."""
        end = datetime.now(tz=UTC)
        start = end - _parse_time_range(request.time_range)
        promql = build_promql(request.query, request.target)
        samples = self._client.query_range(promql, start, end)
        if not samples:
            samples = self._client.query_range(request.query, start, end)
        return TelemetryResult(
            request=request,
            datapoints=_datapoints_from_samples(samples),
            timestamps=_timestamps_from_samples(samples),
        )


def _summarize_series(query: str, target: str, series: list[float]) -> str:
    if query in {
        "container_memory_usage_bytes",
        "container_memory_working_set_bytes",
    }:
        if len(series) >= 2:
            start_mi, end_mi = series[0], series[-1]
            return f"Memory increased from {start_mi}Mi to {end_mi}Mi"
        return f"Insufficient memory samples for {target}"
    if query == "http_requests_per_second":
        if not series:
            return f"No RPS samples for {target}"
        baseline, peak = min(series), max(series)
        if peak >= 3.0 * max(baseline, 1.0):
            return f"Traffic spike rps={peak} for {target}"
        return f"Request rate stable (~{baseline}-{peak} requests/sec) for {target}"
    return f"Prometheus metric result for query={query} target={target}"
