from __future__ import annotations

from pathlib import Path

from incident_agent.datasets.core import write_jsonl
from incident_agent.datasets.crashloopbackoff.generate import make_incident
from incident_agent.eval.freeze_baseline import freeze_stage0_baseline


def test_freeze_stage0_baseline_generates_manifest_and_artifacts(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    incidents = [make_incident(seed=42, idx=i) for i in range(3)]
    write_jsonl(incidents, dataset_path)

    manifest = freeze_stage0_baseline(
        dataset_path=str(dataset_path),
        label="test-baseline",
        artifacts_dir=str(tmp_path / "artifacts"),
    )

    deterministic = manifest["deterministic_stage0"]
    heuristic = manifest["heuristic_benchmark"]

    assert manifest["label"] == "test-baseline"
    assert deterministic["incidents"] == 3
    assert 0.0 <= deterministic["diagnosis_accuracy"] <= 1.0
    assert 0.0 <= deterministic["fix_accuracy"] <= 1.0
    assert Path(deterministic["artifacts"]["run_json"]).exists()
    assert Path(deterministic["artifacts"]["trace_json"]).exists()
    assert Path(heuristic["artifacts"]["report_json"]).exists()
