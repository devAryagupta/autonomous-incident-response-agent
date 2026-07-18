from __future__ import annotations

import json
from pathlib import Path

from incident_agent.datasets.crashloopbackoff.generate import make_incident


def test_default_fixtures_file_exists_and_is_valid_json() -> None:
    p = (
        Path(__file__).parent.parent
        / "src"
        / "incident_agent"
        / "datasets"
        / "crashloopbackoff"
        / "fixtures"
        / "crashloop_fixtures.json"
    )
    assert p.exists()
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1"
    assert "fixtures" in raw


def test_make_incident_uses_fixtures_without_crashing() -> None:
    # This implicitly exercises fixture templating. If fixtures are missing,
    # generator raises RuntimeError.
    inc = make_incident(seed=123, idx=0)
    assert inc.logs
    assert inc.events
    assert inc.root_cause
    assert inc.expected_fix.summary

