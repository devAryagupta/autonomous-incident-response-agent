"""36-case expanded golden set — headline metrics after the live agent run."""

from pathlib import Path

from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.eval.run_golden import run_golden_dataset_with_reasoning

_EXPANDED_PATH = Path(
    "data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset_expanded.jsonl"
)


def test_expanded_golden_agent_headline_metrics() -> None:
    incidents = load_jsonl(CrashLoopBackOffIncident, _EXPANDED_PATH, strict=True)
    assert len(incidents) == 36

    rows, _traces = run_golden_dataset_with_reasoning(incidents)
    diagnosis_correct = sum(row.diagnosis_correct for row in rows)
    fix_correct = sum(row.fix_correct for row in rows)

    # Committed fixes only. Symptom-only OOM (no distinguishing cause
    # evidence) and low-confidence holds stay NOOP and do not count.
    assert diagnosis_correct == 32
    assert fix_correct == 16
