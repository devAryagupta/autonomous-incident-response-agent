from pathlib import Path

from incident_agent.benchmark import run_crashloop_benchmark
from incident_agent.datasets.core import write_jsonl
from incident_agent.datasets.crashloopbackoff.generate import make_incident


def test_oracle_baseline_hits_1_accuracy(tmp_path: Path) -> None:
    dataset_path = tmp_path / "dataset.jsonl"
    incidents = [make_incident(seed=123, idx=i) for i in range(25)]
    write_jsonl(incidents, dataset_path)

    report, artifacts = run_crashloop_benchmark(
        dataset_path=str(dataset_path),
        baseline="oracle",
        out_dir=str(tmp_path / "artifacts"),
    )
    assert report.category_accuracy == 1.0
    assert report.hypothesis_top1_accuracy == 1.0
    assert report.hypothesis_top3_accuracy == 1.0
    assert report.fix_kind_accuracy == 1.0
    assert report.diagnosis_accuracy == 1.0
    assert Path(artifacts.predictions_path).exists()
    assert Path(artifacts.report_path).exists()

