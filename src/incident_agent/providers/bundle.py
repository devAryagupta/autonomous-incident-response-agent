"""Provider bundle: the single DI unit passed into the graph/pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from incident_agent.memory.store import BaseMemoryStore
from incident_agent.providers.dry_run import DryRunExecutionProvider
from incident_agent.providers.memory import LocalIncidentMemoryProvider, NoMemoryProvider
from incident_agent.providers.protocols import (
    ExecutionProvider,
    MemoryProvider,
    MetricsProvider,
    ObservationProvider,
)
from incident_agent.providers.synthetic import (
    SyntheticMetricsProvider,
    SyntheticObservationProvider,
)

if TYPE_CHECKING:
    from incident_agent.execution.kubectl_client import BaseKubectlClient
    from incident_agent.providers.kubernetes import KubernetesClient
    from incident_agent.providers.prometheus import BasePrometheusClient


@dataclass(frozen=True, slots=True)
class ProviderBundle:
    """Injectable set of providers. Swap implementations without changing nodes."""

    observations: ObservationProvider
    metrics: MetricsProvider
    execution: ExecutionProvider
    memory: MemoryProvider


def default_providers() -> ProviderBundle:
    """Stage-0 defaults: synthetic observations/metrics, dry-run, no persistent memory."""
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=NoMemoryProvider(),
    )


def learning_providers(
    *,
    memory_path: str | Path | None = None,
    memory_store: BaseMemoryStore | None = None,
) -> ProviderBundle:
    """Same Stage-0 backends with LocalIncidentMemoryProvider enabled."""
    memory: MemoryProvider
    if memory_store is not None:
        memory = LocalIncidentMemoryProvider(store=memory_store)
    else:
        memory = LocalIncidentMemoryProvider(path=memory_path)
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=memory,
    )


def k8s_observation_providers(
    *,
    client: KubernetesClient | None = None,
    kube_context: str | None = None,
    memory_path: str | Path | None = None,
    memory_store: BaseMemoryStore | None = None,
) -> ProviderBundle:
    """
    Real Kubernetes observations; metrics/execution still Stage-0 stubs.

    Pass an injectable client for tests, or omit to load kubeconfig / in-cluster.
    """
    # Lazy import avoids providers.__init__ ↔ kubernetes circular load.
    from incident_agent.providers.kubernetes import (
        KubernetesObservationProvider,
        live_kubernetes_client,
    )

    k8s_client = client if client is not None else live_kubernetes_client(context=kube_context)
    memory: MemoryProvider
    if memory_store is not None:
        memory = LocalIncidentMemoryProvider(store=memory_store)
    elif memory_path is not None:
        memory = LocalIncidentMemoryProvider(path=memory_path)
    else:
        memory = NoMemoryProvider()
    return ProviderBundle(
        observations=KubernetesObservationProvider(k8s_client),
        metrics=SyntheticMetricsProvider(),
        execution=DryRunExecutionProvider(),
        memory=memory,
    )


def prometheus_metrics_providers(
    *,
    metrics_client: BasePrometheusClient | None = None,
    prometheus_url: str | None = None,
    memory_path: str | Path | None = None,
    memory_store: BaseMemoryStore | None = None,
) -> ProviderBundle:
    """
    Real Prometheus metrics; observations/execution still Stage-0 stubs.

    Pass an injectable BasePrometheusClient (e.g. FakePrometheusClient) for tests,
    or ``prometheus_url`` for live HTTP.
    """
    from incident_agent.providers.prometheus import (
        PrometheusMetricsProvider,
        live_prometheus_client,
    )

    if metrics_client is not None:
        metrics: MetricsProvider = PrometheusMetricsProvider(metrics_client)
    elif prometheus_url:
        metrics = PrometheusMetricsProvider(live_prometheus_client(base_url=prometheus_url))
    else:
        raise ValueError("prometheus_metrics_providers requires metrics_client or prometheus_url")

    memory: MemoryProvider
    if memory_store is not None:
        memory = LocalIncidentMemoryProvider(store=memory_store)
    elif memory_path is not None:
        memory = LocalIncidentMemoryProvider(path=memory_path)
    else:
        memory = NoMemoryProvider()
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=metrics,
        execution=DryRunExecutionProvider(),
        memory=memory,
    )


def kubectl_execution_providers(
    *,
    kubectl_client: BaseKubectlClient | None = None,
    use_live: bool = False,
    kube_context: str | None = None,
    default_dry_run: bool = True,
    memory_path: str | Path | None = None,
    memory_store: BaseMemoryStore | None = None,
) -> ProviderBundle:
    """
    Allowlisted Kubectl execution; observations/metrics still Stage-0 stubs.

    - Tests: pass ``FakeKubectlClient`` (or omit → fake default).
    - Live cluster: ``use_live=True`` (requires optional ``k8s`` extra).
    - ``default_dry_run=True`` blocks real mutations unless state sets
      ``observations.extra["kubectl_dry_run"]=False``.
    """
    from incident_agent.execution.kubectl_client import FakeKubectlClient, live_kubectl_client
    from incident_agent.execution.provider import KubectlExecutionProvider

    if kubectl_client is not None:
        client: BaseKubectlClient = kubectl_client
    elif use_live:
        client = live_kubectl_client(context=kube_context)
    else:
        client = FakeKubectlClient()

    memory: MemoryProvider
    if memory_store is not None:
        memory = LocalIncidentMemoryProvider(store=memory_store)
    elif memory_path is not None:
        memory = LocalIncidentMemoryProvider(path=memory_path)
    else:
        memory = NoMemoryProvider()
    return ProviderBundle(
        observations=SyntheticObservationProvider(),
        metrics=SyntheticMetricsProvider(),
        execution=KubectlExecutionProvider(
            client,
            default_dry_run=default_dry_run,
        ),
        memory=memory,
    )
