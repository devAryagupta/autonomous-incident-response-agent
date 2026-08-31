"""Source-agnostic incident scenarios for benchmarks and agent ingest.

External datasets (Hugging Face, local JSONL, etc.) convert into
``IncidentScenario``. The agent and eval scorecard depend on this model —
never on vendor column names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from incident_agent.contracts import Alert, Observations

AlertSeverity = Literal["info", "warning", "critical"]


class IncidentScenario(BaseModel):
    """Stable internal scenario: only fields the agent / eval use today."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal["1"] = "1"
    scenario_id: str
    title: str
    severity: AlertSeverity
    category: str
    description: str
    symptoms: list[str] = Field(default_factory=list)
    # Ground truth for diagnosis / scorecard (DecisionTrace.ground_truth_cause).
    root_cause: str
    # Expected remediation outline (eval / future planner checks).
    resolution_steps: list[str] = Field(default_factory=list)
    environment: str | None = None
    tags: list[str] = Field(default_factory=list)

    def to_alert(self, *, starts_at: datetime | None = None) -> Alert:
        """Build the contract ``Alert`` the pipeline / graph ingest."""
        labels: dict[str, str] = {"category": self.category}
        if self.environment:
            labels["environment"] = self.environment
        for i, tag in enumerate(self.tags):
            labels[f"tag_{i}"] = tag
        return Alert(
            alert_name=self.title,
            severity=self.severity,
            starts_at=starts_at or datetime.now(tz=UTC),
            labels=labels,
            annotations={"summary": self.description, "root_cause": self.root_cause},
        )

    def to_observations(self) -> Observations:
        """Seed observations from symptoms (no structured logs on text datasets)."""
        logs = list(self.symptoms)
        if self.description and self.description not in logs:
            logs.insert(0, self.description)
        return Observations(
            logs=logs,
            events=[],
            extra={
                "scenario_id": self.scenario_id,
                "category": self.category,
                "tags": list(self.tags),
            },
        )
