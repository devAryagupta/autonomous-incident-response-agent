"""Provider architecture: abstract interfaces + Stage-0 / live backends."""

from incident_agent.llm.provider import LLMSuggestionProvider, NoopLLMSuggestionProvider
from incident_agent.providers.bundle import (
    ProviderBundle,
    default_providers,
    k8s_observation_providers,
    kubectl_execution_providers,
    learning_providers,
    prometheus_metrics_providers,
)
from incident_agent.providers.context import PROVIDERS_CONFIG_KEY, resolve_providers
from incident_agent.providers.dry_run import DryRunExecutionProvider
from incident_agent.providers.fulfill import fulfill_evidence_requests
from incident_agent.providers.kubernetes import (
    ContainerStatusSnapshot,
    EventRecord,
    KubernetesClient,
    KubernetesObservationProvider,
    PodSnapshot,
    live_kubernetes_client,
    observations_from_pod,
    resolve_k8s_target,
    resource_ref_from_pod,
)
from incident_agent.providers.memory import LocalIncidentMemoryProvider, NoMemoryProvider
from incident_agent.providers.prometheus import (
    BasePrometheusClient,
    FakePrometheusClient,
    PrometheusClient,
    PrometheusMetricsProvider,
    StaticPrometheusClient,
    build_promql,
    live_prometheus_client,
    normalize_prometheus_url,
)
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

__all__ = [
    "PROVIDERS_CONFIG_KEY",
    "BasePrometheusClient",
    "ContainerStatusSnapshot",
    "DryRunExecutionProvider",
    "EventRecord",
    "ExecutionProvider",
    "FakePrometheusClient",
    "KubernetesClient",
    "KubernetesObservationProvider",
    "LLMSuggestionProvider",
    "LocalIncidentMemoryProvider",
    "MemoryProvider",
    "MetricsProvider",
    "NoMemoryProvider",
    "NoopLLMSuggestionProvider",
    "ObservationProvider",
    "PodSnapshot",
    "PrometheusClient",
    "PrometheusMetricsProvider",
    "ProviderBundle",
    "StaticPrometheusClient",
    "SyntheticMetricsProvider",
    "SyntheticObservationProvider",
    "build_promql",
    "default_providers",
    "fulfill_evidence_requests",
    "k8s_observation_providers",
    "kubectl_execution_providers",
    "learning_providers",
    "live_kubernetes_client",
    "live_prometheus_client",
    "normalize_prometheus_url",
    "observations_from_pod",
    "prometheus_metrics_providers",
    "resolve_k8s_target",
    "resolve_providers",
    "resource_ref_from_pod",
]
