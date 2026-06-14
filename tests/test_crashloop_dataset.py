import json
from pathlib import Path

from incident_agent.datasets.crashloopbackoff.generate import make_incident
from incident_agent.datasets.crashloopbackoff.schema import CrashLoopBackOffIncident


def test_make_incident_is_valid_pydantic() -> None:
    inc = make_incident(seed=1, idx=0)
    assert inc.incident_type == "CrashLoopBackOff"
    assert inc.alert.alert_name == "CrashLoopBackOff"
    assert inc.expected_fix.summary


def test_json_roundtrip() -> None:
    inc = make_incident(seed=2, idx=10)
    raw = json.dumps(inc.model_dump(mode="json"))
    loaded = CrashLoopBackOffIncident.model_validate_json(raw)
    assert loaded.incident_id == inc.incident_id


def test_writes_jsonl(tmp_path: Path) -> None:
    p = tmp_path / "incidents.jsonl"
    incidents = [make_incident(seed=3, idx=i) for i in range(5)]
    with p.open("w", encoding="utf-8") as f:
        for inc in incidents:
            f.write(json.dumps(inc.model_dump(mode="json")) + "\n")

    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 5
    one = CrashLoopBackOffIncident.model_validate_json(lines[0])
    assert one.category

