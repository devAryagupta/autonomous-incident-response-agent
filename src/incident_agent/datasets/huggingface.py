"""Hugging Face dataset loaders → ``IncidentScenario``.

Verified load path (pandas / Hub docs):

    splits = {
        "train": "data/train.jsonl",
        "validation": "data/validation.jsonl",
        "test": "data/test.jsonl",
    }
    df = pd.read_json(
        "hf://datasets/Snaseem2026/devops-incident-response/" + splits["train"],
        lines=True,
    )

See: https://huggingface.co/datasets/Snaseem2026/devops-incident-response?library=pandas
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from incident_agent.datasets.scenario import AlertSeverity, IncidentScenario

DatasetSplit = Literal["train", "validation", "test"]

DEVOPS_INCIDENT_RESPONSE_ID = "Snaseem2026/devops-incident-response"

# Paths relative to the dataset repo root (Hub pandas ``hf://`` convention).
DEVOPS_INCIDENT_RESPONSE_SPLITS: dict[DatasetSplit, str] = {
    "train": "data/train.jsonl",
    "validation": "data/validation.jsonl",
    "test": "data/test.jsonl",
}

# HF severity → contract Alert severity (info | warning | critical).
_SEVERITY_MAP: dict[str, AlertSeverity] = {
    "critical": "critical",
    "high": "warning",
    "medium": "warning",
    "low": "info",
    "warning": "warning",
    "info": "info",
}

def _as_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, Mapping):
        return []
    try:
        return [str(item).strip() for item in value if str(item).strip()]
    except TypeError:
        text = str(value).strip()
        return [text] if text else []


def map_hf_severity(raw: str | None) -> AlertSeverity:
    """Map Hub severity labels onto our Alert severity literals."""
    if raw is None or not str(raw).strip():
        return "warning"
    key = str(raw).strip().lower()
    return _SEVERITY_MAP.get(key, "warning")


def hf_row_to_scenario(row: Mapping[str, Any]) -> IncidentScenario:
    """Convert one Hub row into ``IncidentScenario`` (used fields only)."""
    scenario_id = str(row.get("incident_id") or "").strip()
    title = str(row.get("title") or "").strip()
    description = str(row.get("description") or "").strip()
    root_cause = str(row.get("root_cause") or "").strip()
    category = str(row.get("category") or "").strip() or "unknown"

    if not scenario_id:
        raise ValueError("HF row missing required field: incident_id")
    if not title:
        raise ValueError(f"HF row {scenario_id!r} missing required field: title")
    if not root_cause:
        raise ValueError(f"HF row {scenario_id!r} missing required field: root_cause")

    environment = row.get("environment")
    env = str(environment).strip() if environment not in (None, "") else None

    return IncidentScenario(
        scenario_id=scenario_id,
        title=title,
        severity=map_hf_severity(
            None if row.get("severity") is None else str(row.get("severity"))
        ),
        category=category,
        description=description,
        symptoms=_as_str_list(row.get("symptoms")),
        root_cause=root_cause,
        resolution_steps=_as_str_list(row.get("resolution_steps")),
        environment=env,
        tags=_as_str_list(row.get("tags")),
    )


def scenarios_from_records(records: Iterable[Mapping[str, Any]]) -> list[IncidentScenario]:
    """Convert an iterable of row mappings (DataFrame rows, dicts, fixtures)."""
    return [hf_row_to_scenario(row) for row in records]


def load_devops_incident_response(
    split: DatasetSplit = "train",
) -> list[IncidentScenario]:
    """
    Load ``Snaseem2026/devops-incident-response`` and return ``IncidentScenario``s.

    Requires optional deps: ``pandas`` (+ ``pyarrow`` / ``huggingface_hub`` for ``hf://``).
    Install: ``pip install 'incident-response-agent[huggingface]'``
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError(
            "pandas is required to load Hugging Face datasets. "
            "Install with: pip install 'incident-response-agent[huggingface]'"
        ) from e

    if split not in DEVOPS_INCIDENT_RESPONSE_SPLITS:
        allowed = ", ".join(sorted(DEVOPS_INCIDENT_RESPONSE_SPLITS))
        raise ValueError(f"Unknown split {split!r}; expected one of: {allowed}")

    relative = DEVOPS_INCIDENT_RESPONSE_SPLITS[split]
    uri = f"hf://datasets/{DEVOPS_INCIDENT_RESPONSE_ID}/{relative}"
    frame = pd.read_json(uri, lines=True)
    records = frame.to_dict(orient="records")
    return scenarios_from_records(records)


__all__ = [
    "DEVOPS_INCIDENT_RESPONSE_ID",
    "DEVOPS_INCIDENT_RESPONSE_SPLITS",
    "DatasetSplit",
    "hf_row_to_scenario",
    "load_devops_incident_response",
    "map_hf_severity",
    "scenarios_from_records",
]
