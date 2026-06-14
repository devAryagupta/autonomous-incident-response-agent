import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from incident_agent.datasets.core import DatasetLoadError, load_jsonl


class _Toy(BaseModel):
    x: int


def test_load_jsonl_strict_success(tmp_path: Path) -> None:
    p = tmp_path / "ok.jsonl"
    p.write_text(json.dumps({"x": 1}) + "\n" + json.dumps({"x": 2}) + "\n", encoding="utf-8")
    items = load_jsonl(_Toy, p, strict=True)
    assert [i.x for i in items] == [1, 2]


def test_load_jsonl_strict_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.jsonl"
    p.write_text("{not-json}\n" + json.dumps({"x": "nope"}) + "\n", encoding="utf-8")
    with pytest.raises(DatasetLoadError) as e:
        load_jsonl(_Toy, p, strict=True)
    assert "Dataset load failed" in str(e.value)


def test_load_jsonl_non_strict_skips(tmp_path: Path) -> None:
    p = tmp_path / "mixed.jsonl"
    p.write_text(
        json.dumps({"x": 1}) + "\n" + "{not-json}\n" + json.dumps({"x": 2}) + "\n",
        encoding="utf-8",
    )
    items = load_jsonl(_Toy, p, strict=False)
    assert [i.x for i in items] == [1, 2]

