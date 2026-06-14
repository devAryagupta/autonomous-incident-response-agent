import json
from pathlib import Path

from incident_agent.datasets.core import load_jsonl
from incident_agent.datasets.crashloopbackoff.generate import make_incident
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident
from incident_agent.datasets.eval import CrashLoopPrediction, evaluate_crashloop


def test_evaluate_category_accuracy() -> None:
    incs = [make_incident(seed=7, idx=i) for i in range(10)]
    preds = [
        CrashLoopPrediction(
            incident_id=inc.incident_id,
            predicted_category=inc.category,
            predicted_root_cause="some cause",
            predicted_fix_summary="some fix",
        )
        for inc in incs
    ]
    report = evaluate_crashloop(incs, preds)
    assert report.category_accuracy == 1.0


def test_eval_handles_missing_predictions() -> None:
    incs = [make_incident(seed=9, idx=i) for i in range(5)]
    preds = [
        CrashLoopPrediction(
            incident_id=incs[0].incident_id,
            predicted_category=incs[0].category,
        )
    ]
    report = evaluate_crashloop(incs, preds)
    assert 0.0 <= report.category_accuracy <= 1.0


def test_loader_reads_real_dataset_file_if_present(tmp_path: Path) -> None:
    p = tmp_path / "inc.jsonl"
    incs = [make_incident(seed=1, idx=i) for i in range(3)]
    p.write_text(
        "\n".join(json.dumps(i.model_dump(mode="json")) for i in incs) + "\n",
        encoding="utf-8",
    )

    loaded = load_jsonl(CrashLoopBackOffIncident, p, strict=True)
    assert len(loaded) == 3

