"""Resolve ProviderBundle from LangGraph RunnableConfig or fall back to defaults."""

from __future__ import annotations

from typing import Any

from incident_agent.providers.bundle import ProviderBundle, default_providers

PROVIDERS_CONFIG_KEY = "providers"


def resolve_providers(config: dict[str, Any] | None = None) -> ProviderBundle:
    """
    Dependency-injection lookup.

    LangGraph invoke:
      GRAPH.invoke(state, config={"configurable": {"providers": bundle}})
    """
    if not config:
        return default_providers()

    # LangGraph may pass the full RunnableConfig or an already-unwrapped
    # configurable dict, depending on version / node signature.
    candidates = (
        config.get(PROVIDERS_CONFIG_KEY),
        (config.get("configurable") or {}).get(PROVIDERS_CONFIG_KEY),
    )
    for providers in candidates:
        if isinstance(providers, ProviderBundle):
            return providers
    return default_providers()
