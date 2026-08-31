"""Unit tests for Hugging Face → IncidentScenario conversion (offline fixtures)."""

from __future__ import annotations

import pytest

from incident_agent.datasets.huggingface import (
    DEVOPS_INCIDENT_RESPONSE_ID,
    DEVOPS_INCIDENT_RESPONSE_SPLITS,
    hf_row_to_scenario,
    map_hf_severity,
    scenarios_from_records,
)
from incident_agent.datasets.scenario import IncidentScenario

# Minimal Hub-shaped rows (only fields we map). Extra keys must be ignored.
_HF_FIXTURE_ROW = {
    "incident_id": "INC-2024-001",
    "title": "Production Kubernetes Cluster - Pods CrashLooping",
    "severity": "critical",
    "category": "kubernetes",
    "environment": "production",
    "description": "Multiple pods are in CrashLoopBackOff state.",
    "symptoms": [
        "Pods restarting every 30-60 seconds",
        "kubectl get pods shows CrashLoopBackOff status",
    ],
    "troubleshooting_conversation": [
        {"role": "oncall_engineer", "message": "Checking logs", "timestamp": "2024-03-15T14:23:00Z"}
    ],
    "root_cause": "PostgreSQL pod crashed due to OOMKilled (Out of Memory).",
    "resolution_steps": [
        "Scaled memory limit from 2Gi to 4Gi",
        "Restarted the PostgreSQL pod",
    ],
    "prevention_measures": ["Set memory alerts at 80%"],
    "time_to_detect": "5 minutes",
    "time_to_resolve": "25 minutes",
    "impact": "15% of user requests failed",
    "tags": ["kubernetes", "oom", "crashloop"],
    "related_technologies": ["Kubernetes", "PostgreSQL"],
}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("critical", "critical"),
        ("high", "warning"),
        ("medium", "warning"),
        ("low", "info"),
        ("WARNING", "warning"),
        (None, "warning"),
        ("", "warning"),
        ("mystery", "warning"),
    ],
)
def test_map_hf_severity(raw: str | None, expected: str) -> None:
    assert map_hf_severity(raw) == expected


def test_hf_row_to_scenario_maps_used_fields_only() -> None:
    scenario = hf_row_to_scenario(_HF_FIXTURE_ROW)

    assert isinstance(scenario, IncidentScenario)
    assert scenario.scenario_id == "INC-2024-001"
    assert scenario.title.startswith("Production Kubernetes")
    assert scenario.severity == "critical"
    assert scenario.category == "kubernetes"
    assert scenario.environment == "production"
    assert "CrashLoopBackOff" in scenario.description
    assert len(scenario.symptoms) == 2
    assert "OOMKilled" in scenario.root_cause
    assert scenario.resolution_steps[0].startswith("Scaled memory")
    assert scenario.tags == ["kubernetes", "oom", "crashloop"]
    # Ignored Hub fields must not appear on the internal model.
    dumped = scenario.model_dump()
    assert "troubleshooting_conversation" not in dumped
    assert "prevention_measures" not in dumped
    assert "impact" not in dumped
    assert "related_technologies" not in dumped


def test_scenarios_from_records() -> None:
    rows = [
        _HF_FIXTURE_ROW,
        {
            **_HF_FIXTURE_ROW,
            "incident_id": "INC-2024-002",
            "severity": "high",
            "title": "CI/CD Pipeline Failing",
            "root_cause": "DNS resolver outage on Jenkins agents.",
        },
    ]
    scenarios = scenarios_from_records(rows)
    assert len(scenarios) == 2
    assert scenarios[1].severity == "warning"
    assert scenarios[1].scenario_id == "INC-2024-002"


def test_hf_row_requires_incident_id_and_root_cause() -> None:
    with pytest.raises(ValueError, match="incident_id"):
        hf_row_to_scenario({**_HF_FIXTURE_ROW, "incident_id": ""})
    with pytest.raises(ValueError, match="root_cause"):
        hf_row_to_scenario({**_HF_FIXTURE_ROW, "root_cause": "  "})


def test_scenario_to_alert_and_observations() -> None:
    scenario = hf_row_to_scenario(_HF_FIXTURE_ROW)
    alert = scenario.to_alert()
    assert alert.alert_name == scenario.title
    assert alert.severity == "critical"
    assert alert.labels["category"] == "kubernetes"
    assert alert.labels["environment"] == "production"
    assert alert.annotations["summary"] == scenario.description

    obs = scenario.to_observations()
    assert scenario.description in obs.logs
    assert scenario.symptoms[0] in obs.logs
    assert obs.extra["scenario_id"] == "INC-2024-001"


def test_split_paths_match_hub_docs() -> None:
    assert DEVOPS_INCIDENT_RESPONSE_ID == "Snaseem2026/devops-incident-response"
    assert DEVOPS_INCIDENT_RESPONSE_SPLITS == {
        "train": "data/train.jsonl",
        "validation": "data/validation.jsonl",
        "test": "data/test.jsonl",
    }
