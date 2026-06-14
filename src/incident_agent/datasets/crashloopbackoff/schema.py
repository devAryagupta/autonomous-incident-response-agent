from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CrashLoopCategory = Literal[
    "missing_env_var",
    "bad_env_var_value",
    "missing_configmap",
    "missing_secret",
    "misconfigured_volume_mount",
    "dependency_unavailable",
    "app_bug_unhandled_exception",
    "resource_constraint_oom",
    "resource_constraint_cpu",
]


class K8sRef(BaseModel):
    namespace: str
    kind: Literal["Deployment", "StatefulSet", "Pod"]
    name: str


class Alert(BaseModel):
    alert_name: Literal["CrashLoopBackOff"]
    severity: Literal["warning", "critical"] = "critical"
    starts_at: datetime
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class Evidence(BaseModel):
    source: Literal["logs", "events", "describe", "metrics"]
    text: str


class ExpectedFix(BaseModel):
    """Ground-truth target outcome; not necessarily the only valid fix."""

    summary: str
    kind: str | None = None
    kubectl_hint: str | None = None


class CrashLoopBackOffIncident(BaseModel):
    schema_version: Literal["1"] = "1"
    incident_type: Literal["CrashLoopBackOff"] = "CrashLoopBackOff"
    incident_id: str
    created_at: datetime

    target: K8sRef
    alert: Alert

    # Observability payloads (synthetic but realistic)
    logs: list[str] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)

    # Labels for evaluation
    category: CrashLoopCategory
    root_cause: str
    expected_fix: ExpectedFix

    # Optional extra context for harder examples
    distractors: list[str] = Field(default_factory=list)

