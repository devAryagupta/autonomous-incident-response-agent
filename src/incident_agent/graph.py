from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from incident_agent.contracts import IncidentState
from incident_agent.nodes import (
    approve,
    collect_evidence,
    compute_confidence,
    diagnose,
    execute_fix,
    hypothesize,
    plan_fix,
    pre_execute_validate,
    prepare_execution,
    validate_fix,
    verify_hypotheses,
    verify_outcome,
)
from incident_agent.nodes.enrich import enrich
from incident_agent.providers import PROVIDERS_CONFIG_KEY, ProviderBundle, default_providers
from incident_agent.routing import (
    bump_replan,
    escalate_insufficient_confidence,
    finalize,
    route_on_confidence,
)

try:
    from langgraph.graph import END, START, StateGraph
except Exception as e:  # pragma: no cover
    raise ImportError(
        "LangGraph is not installed. Install with `pip install langgraph` "
        "or `pip install .[agent]`."
    ) from e


def _config_from_runnable(config: RunnableConfig | None) -> dict[str, Any] | None:
    if config is None:
        return None
    if isinstance(config, dict):
        return dict(config)
    try:
        return dict(config)
    except Exception:
        return None


def _with_phase(*, phase: str, fn):
    """Wrap a pure reasoning node (state → partial update) and set phase."""

    def _wrapped(
        state: IncidentState,
        config: RunnableConfig | None = None,
    ) -> dict[str, object]:
        _ = config
        updates = dict(fn(state))
        updates["phase"] = phase
        return updates

    return _wrapped


def _enrich_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    return enrich(state, config=_config_from_runnable(config))


def _collect_evidence_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    updates = dict(collect_evidence(state, config=_config_from_runnable(config)))
    updates["phase"] = "collect_evidence"
    return updates


def _confidence_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    _ = config
    updates = dict(compute_confidence(state))
    updates["phase"] = "score_confidence"
    return updates


def _execute_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    updates = dict(execute_fix(state, config=_config_from_runnable(config)))
    updates["phase"] = "execute"
    return updates


def _verify_outcome_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    updates = dict(verify_outcome(state, config=_config_from_runnable(config)))
    updates["phase"] = "verify_outcome"
    return updates


def _finalize_node(
    state: IncidentState,
    config: RunnableConfig | None = None,
) -> dict[str, object]:
    return finalize(state, config=_config_from_runnable(config))


def build_graph():
    """
    Provider-aware LangGraph with confidence replan + execution lifecycle.

    START -> enrich -> diagnose -> hypothesize -> collect_evidence
          -> verify_hypotheses -> plan_fix -> validate_fix -> confidence
      ├── high confidence → prepare_execution → … → execute → finalize
      ├── low confidence & retries → replan → hypothesize ↺
      └── still low after max replans → escalate (NOOP) → finalize

    Providers are injected via:
      invoke(state, config={"configurable": {"providers": ProviderBundle(...)}})
    """
    g = StateGraph(IncidentState)

    g.add_node("enrich", _enrich_node)
    g.add_node("diagnose", _with_phase(phase="diagnose", fn=diagnose))
    g.add_node("hypothesize", _with_phase(phase="hypothesize", fn=hypothesize))
    g.add_node("collect_evidence", _collect_evidence_node)
    g.add_node(
        "verify_hypotheses",
        _with_phase(phase="verify_hypotheses", fn=verify_hypotheses),
    )
    g.add_node("plan_fix", _with_phase(phase="plan_fix", fn=plan_fix))
    g.add_node("validate_fix", _with_phase(phase="validate_fix", fn=validate_fix))
    g.add_node("confidence", _confidence_node)
    g.add_node("replan", bump_replan)
    g.add_node("escalate", _with_phase(phase="escalate", fn=escalate_insufficient_confidence))
    g.add_node(
        "prepare_execution",
        _with_phase(phase="prepare_execution", fn=prepare_execution),
    )
    g.add_node(
        "pre_execute_validate",
        _with_phase(phase="pre_execute_validate", fn=pre_execute_validate),
    )
    g.add_node("approve", _with_phase(phase="approve", fn=approve))
    g.add_node("execute", _execute_node)
    g.add_node("verify_outcome", _verify_outcome_node)
    g.add_node("finalize", _finalize_node)

    g.add_edge(START, "enrich")
    g.add_edge("enrich", "diagnose")
    g.add_edge("diagnose", "hypothesize")
    g.add_edge("hypothesize", "collect_evidence")
    g.add_edge("collect_evidence", "verify_hypotheses")
    g.add_edge("verify_hypotheses", "plan_fix")
    g.add_edge("plan_fix", "validate_fix")
    g.add_edge("validate_fix", "confidence")

    g.add_conditional_edges(
        "confidence",
        route_on_confidence,
        {
            "execute": "prepare_execution",
            "replan": "replan",
            "escalate": "escalate",
        },
    )
    g.add_edge("replan", "hypothesize")
    g.add_edge("escalate", "finalize")
    g.add_edge("prepare_execution", "pre_execute_validate")
    g.add_edge("pre_execute_validate", "approve")
    g.add_edge("approve", "execute")
    g.add_edge("execute", "verify_outcome")
    g.add_edge("verify_outcome", "finalize")
    g.add_edge("finalize", END)

    return g.compile()


class IncidentStateGraph:
    """Thin wrapper so `invoke()` always returns an `IncidentState` contract."""

    def __init__(self, app, *, providers: ProviderBundle | None = None) -> None:
        self._app = app
        self._providers = providers or default_providers()

    def invoke(
        self,
        state: IncidentState,
        *,
        providers: ProviderBundle | None = None,
    ) -> IncidentState:
        bundle = providers or self._providers
        out = self._app.invoke(
            state,
            config={"configurable": {PROVIDERS_CONFIG_KEY: bundle}},
        )
        if isinstance(out, IncidentState):
            return out
        return IncidentState.model_validate(out)


GRAPH = IncidentStateGraph(build_graph())
