"""Repeatable live-pod entry point: build state → providers → GRAPH → print.

Reasoning stays in the graph. This module does not diagnose or plan.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from incident_agent.contracts import (
    Alert,
    IncidentState,
    Observations,
    ResourceRef,
)
from incident_agent.graph import GRAPH
from incident_agent.providers import ProviderBundle, k8s_observation_providers
from incident_agent.verification.conclusion import summarize_cause_resolution

DEFAULT_NAMESPACE = "default"
DEFAULT_POD = "oom-demo"
DEFAULT_PROMETHEUS_URL = "http://localhost:9090"


def incident_state_for_pod(*, namespace: str, name: str) -> IncidentState:
    """Seed ingest state that names a Kubernetes pod. No cluster I/O."""
    now = datetime.now(tz=UTC)
    target_ref = f"pod/{name}"
    return IncidentState(
        incident_id=f"live-{namespace}-{name}",
        created_at=now,
        phase="ingest",
        resource=ResourceRef(
            system="kubernetes",
            namespace=namespace,
            kind="Pod",
            name=name,
        ),
        alert=Alert(
            alert_name="KubePodCrashLooping",
            severity="critical",
            starts_at=now,
            labels={"namespace": namespace, "pod": name},
        ),
        observations=Observations(extra={"target_ref": target_ref, "top_n": 3}),
    )


def run_live_pod(
    *,
    namespace: str = DEFAULT_NAMESPACE,
    pod: str = DEFAULT_POD,
    kube_context: str | None = None,
    prometheus_url: str | None = DEFAULT_PROMETHEUS_URL,
    providers: ProviderBundle | None = None,
) -> IncidentState:
    """Invoke the compiled graph. Inject ``providers`` in tests."""
    state = incident_state_for_pod(namespace=namespace, name=pod)
    bundle = providers or k8s_observation_providers(
        kube_context=kube_context,
        prometheus_url=prometheus_url,
    )
    return GRAPH.invoke(state, providers=bundle)


def _lines_or_none(lines: list[str]) -> list[str]:
    return lines if lines else ["<none>"]


def print_run_report(state: IncidentState) -> None:
    """Print the graph result. Presentation only."""
    extra = state.observations.extra
    print(f"Incident: {state.incident_id}")
    print(f"Target: {extra.get('target_ref', '<none>')}")
    print(f"Provider: {extra.get('observation_provider', '<none>')}")
    print(f"Metrics: {extra.get('metrics_provider', '<none>')}")
    if extra.get("error"):
        print(f"Error: {extra['error']}")
    print()
    _print_diagnosis(state)
    _print_evidence(state)
    _print_hypotheses(state)
    _print_conclusion(state)
    _print_remediation(state)
    _print_execution(state)


def _print_diagnosis(state: IncidentState) -> None:
    print("Diagnosis:")
    if state.diagnosis is None:
        print("<none>")
    else:
        print(state.diagnosis.category or state.diagnosis.summary)
    print()


def _print_evidence(state: IncidentState) -> None:
    print("Evidence:")
    lines: list[str] = []
    if state.diagnosis is not None:
        lines.extend(f"  {item.text}" for item in state.diagnosis.evidence)
    for line in _lines_or_none(lines):
        print(line)
    print()


def _verification_by_id(state: IncidentState) -> dict[str, str]:
    return {item.hypothesis_id: item.result for item in state.hypothesis_verifications}


def _print_hypotheses(state: IncidentState) -> None:
    print("Hypotheses:")
    if not state.hypotheses:
        print("<none>")
    else:
        results = _verification_by_id(state)
        width = max(len(item.description) for item in state.hypotheses)
        for hypothesis in state.hypotheses:
            verdict = results.get(hypothesis.hypothesis_id, "unverified")
            print(f"  {hypothesis.description:<{width}}  {verdict}")
    print()


def _print_conclusion(state: IncidentState) -> None:
    print("Conclusion:")
    print(f"  {summarize_cause_resolution(state)}")
    print()


def _print_remediation(state: IncidentState) -> None:
    print("Remediation:")
    lines: list[str] = []
    if state.fix_plan is not None:
        lines.append(f"risk={state.fix_plan.risk.value}")
        for action in state.fix_plan.actions:
            params = action.params if isinstance(action.params, dict) else {}
            label = params.get("change") or action.action_type.value
            lines.append(f"- {label}")
    for option in state.remediation_options:
        marker = " (chosen)" if option.option_id == state.chosen_remediation_id else ""
        lines.append(f"- {option.action}{marker}")
    for line in _lines_or_none(lines):
        print(line)
    print()


def _print_execution(state: IncidentState) -> None:
    print("Execution:")
    if state.execution is None:
        print("<none>")
        if state.decision:
            print(f"decision={state.decision} reason={state.decision_reason}")
        return
    print(f"status={state.execution.status} action={state.execution.action}")
    for change in state.execution.applied_changes:
        print(f"- {change}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the incident graph against one live Kubernetes pod. "
            "Observations come from kubeconfig; metrics default to Prometheus "
            f"at {DEFAULT_PROMETHEUS_URL}; execution stays dry-run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m incident_agent.cli.live_pod\n"
            "  python -m incident_agent.cli.live_pod --namespace default --pod oom-demo\n"
            "  python run_live_pod.py --kube-context minikube\n"
            "  python -m incident_agent.cli.live_pod --no-prometheus\n"
        ),
    )
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"Pod namespace (default: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument(
        "--pod",
        default=DEFAULT_POD,
        help=f"Pod name (default: {DEFAULT_POD})",
    )
    parser.add_argument(
        "--kube-context",
        default=None,
        help="kubeconfig context (default: current context)",
    )
    parser.add_argument(
        "--prometheus-url",
        default=DEFAULT_PROMETHEUS_URL,
        help=(
            "Prometheus API root (default: "
            f"{DEFAULT_PROMETHEUS_URL}). UI paths such as /query are stripped."
        ),
    )
    parser.add_argument(
        "--no-prometheus",
        action="store_true",
        help="Skip Prometheus and keep synthetic metrics",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        state = run_live_pod(
            namespace=args.namespace,
            pod=args.pod,
            kube_context=args.kube_context,
            prometheus_url=None if args.no_prometheus else args.prometheus_url,
        )
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print('  pip install -e ".[k8s]"', file=sys.stderr)
        print(
            "  python -m incident_agent.cli.live_pod --namespace default --pod oom-demo",
            file=sys.stderr,
        )
        return 2
    print_run_report(state)
    if state.observations.extra.get("error"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
