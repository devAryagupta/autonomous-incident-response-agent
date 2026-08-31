"""Tests for golden-dataset Stage-0 batch runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from incident_agent.datasets.crashloopbackoff.schema import (
    Alert,
    CrashLoopBackOffIncident,
    ExpectedFix,
    K8sRef,
)
from incident_agent.eval.run_golden import (
    GoldenRunRow,
    row_from_run,
    run_golden_dataset,
    state_from_incident,
    write_golden_report,
)


def _oom_incident() -> CrashLoopBackOffIncident:
    now = datetime(2026, 8, 29, 10, 0, 0, tzinfo=UTC)
    return CrashLoopBackOffIncident(
        incident_id="cl-oom-test",
        created_at=now,
        target=K8sRef(namespace="payments", kind="Deployment", name="order-service"),
        alert=Alert(alert_name="CrashLoopBackOff", severity="critical", starts_at=now),
        logs=[
            "Out of memory: Kill process 1 (java) score 1021 or sacrifice child",
            "java.lang.OutOfMemoryError: Java heap space",
            "Exit Code: 137",
        ],
        events=[
            "Warning OOMKilling pod/order-service Reason: OOMKilled, Exit Code: 137",
        ],
        category="oom",
        root_cause="Container exceeded memory limit (Exit Code: 137).",
        expected_fix=ExpectedFix(
            summary="Raise memory limit",
            kind="increase_memory_limit",
        ),
    )


def test_state_from_incident_sets_target_ref() -> None:
    inc = _oom_incident()
    state = state_from_incident(inc)
    assert state.observations.extra["target_ref"] == "Deployment/order-service"
    assert state.observations.logs == inc.logs


def test_run_golden_dataset_one_incident() -> None:
    rows = run_golden_dataset([_oom_incident()])
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, GoldenRunRow)
    assert row.incident_id == "cl-oom-test"
    assert row.expected_fix_kind == "increase_memory_limit"
    assert row.validation_result in {"PASS", "FAIL"}
    assert row.final_confidence is not None


def test_write_golden_report(tmp_path: Path) -> None:
    row = GoldenRunRow(
        incident_id="x",
        predicted_root_cause="OOMKilled",
        expected_root_cause="OOM",
        diagnosis_correct=True,
        predicted_fix_kind="increase_memory_limit",
        expected_fix_kind="increase_memory_limit",
        fix_correct=True,
        validation_result="PASS",
        final_confidence=0.8,
    )
    out_json = tmp_path / "report.json"
    out_csv = tmp_path / "report.csv"
    write_golden_report([row], out_json=out_json, out_csv=out_csv)
    loaded = json.loads(out_json.read_text(encoding="utf-8"))
    assert loaded[0]["incident_id"] == "x"
    assert "incident_id" in out_csv.read_text(encoding="utf-8")


def test_golden_file_on_disk_has_nine_rows() -> None:
    path = Path("data/synthetic/crashloopbackoff/goldendataset/crashloop_golden_dataset.jsonl")
    if not path.exists():
        return
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 9
