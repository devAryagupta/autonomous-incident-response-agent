from __future__ import annotations

from incident_agent.contracts import IncidentState, Observations
from incident_agent.nodes import compute_confidence, diagnose, hypothesize, plan_fix, validate_fix

try:
    from langgraph.graph import END, START, StateGraph
except Exception as e:  # pragma: no cover
    raise ImportError(
        "LangGraph is not installed. Install with `pip install langgraph` "
        "or `pip install .[agent]`."
    ) from e


def _with_phase(*, phase: str, fn):
    """Wrap a node so it returns a partial state update and sets `phase`."""

    def _wrapped(state: IncidentState) -> dict[str, object]:
        updates = dict(fn(state))
        updates["phase"] = phase
        return updates

    return _wrapped


def confidence(state: IncidentState) -> dict[str, object]:
    """Final node: score confidence and mark the lifecycle done."""
    updates = dict(compute_confidence(state))
    updates["phase"] = "done"
    return updates


def build_graph():
    """
    Straight-line LangGraph orchestration.

    START -> diagnose -> hypothesize -> plan_fix -> validate_fix -> confidence -> END
    """
    g = StateGraph(IncidentState)

    g.add_node("diagnose", _with_phase(phase="diagnose", fn=diagnose))
    g.add_node("hypothesize", _with_phase(phase="hypothesize", fn=hypothesize))
    g.add_node("plan_fix", _with_phase(phase="plan_fix", fn=plan_fix))
    g.add_node("validate_fix", _with_phase(phase="validate_fix", fn=validate_fix))
    g.add_node("confidence", confidence)

    g.add_edge(START, "diagnose")
    g.add_edge("diagnose", "hypothesize")
    g.add_edge("hypothesize", "plan_fix")
    g.add_edge("plan_fix", "validate_fix")
    g.add_edge("validate_fix", "confidence")
    g.add_edge("confidence", END)

    return g.compile()


class IncidentStateGraph:
    """Thin wrapper so `invoke()` always returns an `IncidentState` contract."""

    def __init__(self, app) -> None:
        self._app = app

    def invoke(self, state: IncidentState) -> IncidentState:
        out = self._app.invoke(state)
        if isinstance(out, IncidentState):
            return out
        return IncidentState.model_validate(out)


GRAPH = IncidentStateGraph(build_graph())
