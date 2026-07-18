# Autonomous Incident-Response Agent (progressive build)

We’re building this project **progressively in stages** following the plan in:
`C:\Users\ag551\.claude\plans\https-github-com-madhurprash-langgraph-a-velvety-peacock.md`.

## Stage 0 (current)

- Project skeleton + **synthetic incident dataset** for the first incident class:
  **Kubernetes `CrashLoopBackOff`**.
- Deterministic reasoning lifecycle + **LangGraph straight-line orchestration**
  (`START → diagnose → hypothesize → plan_fix → validate_fix → confidence → END`).
  Success criterion: `GRAPH.invoke(state)` matches `run_deterministic_lifecycle()`.

## Quickstart

### Create venv + install

```powershell
cd "d:\autonomous incident-response agent"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev]"
```

### Generate dataset

```powershell
python -m incident_agent.datasets.crashloopbackoff.generate --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" --n 50
```

### Evaluate predictions (dataset layer)

1) Prepare a predictions file (`.jsonl`), one JSON per incident id, e.g.:

```json
{"schema_version":"1","incident_id":"clb-...","predicted_category":"missing_env_var","predicted_root_cause":"...","predicted_fix_summary":"..."}
```

2) Run:

```powershell
python -m incident_agent.datasets.eval --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" --predictions ".\predictions.jsonl"
```

### Run tests

```powershell
pytest
```

## Benchmark runner

Runs a benchmark over a dataset and writes artifacts:

- `predictions.<baseline>.jsonl`
- `report.<baseline>.json`

Example:

```powershell
$env:PYTHONPATH=(Resolve-Path .\src).Path
py -m incident_agent.benchmark --task crashloopbackoff --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" --baseline heuristic --out-dir ".\artifacts\benchmarks\crashloop"
```

Baselines:

- `oracle`: copies ground-truth from dataset (sanity check, should score 1.0 category accuracy)
- `empty`: emits no predictions (lower bound)
- `heuristic`: simple keyword matcher (no AI) to validate end-to-end plumbing
