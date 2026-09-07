from __future__ import annotations

from pathlib import Path

from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident

_EXPANDED_PATH = Path(
    "data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset_expanded.jsonl"
)


def _cause_from_category(category: str) -> str:
    if category == "oom":
        return "OOMKilled"
    if category in {"missing_env_var", "bad_config", "missing_secret"}:
        return "Invalid Configuration"
    if category in {"startup_exception", "app_bug_unhandled_exception"}:
        return "Application Failure"
    return "Other"


def test_expanded_golden_dataset_loads_and_has_expected_size() -> None:
    assert _EXPANDED_PATH.exists()
    incidents = load_jsonl(CrashLoopBackOffIncident, _EXPANDED_PATH, strict=True)
    assert 30 <= len(incidents) <= 50


def test_expanded_golden_dataset_stays_within_three_causes() -> None:
    incidents = load_jsonl(CrashLoopBackOffIncident, _EXPANDED_PATH, strict=True)

    cause_counts: dict[str, int] = {
        "OOMKilled": 0,
        "Invalid Configuration": 0,
        "Application Failure": 0,
    }
    for incident in incidents:
        cause = _cause_from_category(incident.category)
        assert cause in cause_counts
        cause_counts[cause] += 1

    for count in cause_counts.values():
        assert 10 <= count <= 15


def test_expanded_golden_dataset_includes_ambiguous_startup_cases() -> None:
    incidents = load_jsonl(CrashLoopBackOffIncident, _EXPANDED_PATH, strict=True)
    blobs = ["\n".join(i.logs + i.events).lower() for i in incidents]

    assert any(
        "crashloopbackoff" in blob and "exit code: 1" in blob and "generic startup error" in blob
        for blob in blobs
    )
