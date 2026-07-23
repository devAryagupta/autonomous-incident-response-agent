# Autonomous Incident-Response Agent (progressive build)

We’re building this project **progressively in stages** following the plan in:
`C:\Users\ag551\.claude\plans\https-github-com-madhurprash-langgraph-a-velvety-peacock.md`.

## Contributing & coding standards

Before changing code, read:

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup, checks, where to put new code, PR checklist
- **[docs/CODING_PRINCIPLES.md](docs/CODING_PRINCIPLES.md)** — layered architecture, naming, size limits, SoC, runtime vs source

Cursor agents also load `.cursor/rules/` (architecture + Python style). Runtime data belongs under `runtime/`, not `src/`.

## Stage 0 (current)

- Project skeleton + **synthetic incident dataset** for the first incident class:
  **Kubernetes `CrashLoopBackOff`**.
- Deterministic reasoning lifecycle + **LangGraph orchestration** with confidence replan.
- **Provider architecture** (Observation / Metrics / Execution / Memory) so nodes stay
  source-agnostic. Stage-0 backends: Synthetic + DryRun + NoMemory.
  **Live observations (opt-in):** `KubernetesObservationProvider` via
  `k8s_observation_providers()` — cluster logs/events map into existing `Observations`
  so diagnose / hypothesize / verify / remediate stay unchanged.
  **Live metrics (opt-in):** `PrometheusMetricsProvider` via
  `prometheus_metrics_providers()` — PromQL series feed the Bayesian verification loop.
  **Calibration:** `ConfidenceCalibrator` blends posterior × evidence × memory and
  gates mutation when calibrated confidence is below the safety threshold.
  **Execution (opt-in):** `KubectlExecutionProvider` — allowlisted actions
  (`restart_pod`, `rollout_restart`, `scale_deployment`, `update_resource_limit`)
  gated by `ExecutionPolicy` × calibrated confidence. Fake client for tests;
  live via `kubectl_execution_providers(use_live=True)` (optional ``k8s`` extra).

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

### Real Kubernetes observations (opt-in)

Stage-0 defaults stay synthetic. To read a live cluster:

```powershell
pip install -e ".[k8s]"
```

```python
from incident_agent.providers import k8s_observation_providers
from incident_agent.graph import GRAPH

# Uses kubeconfig / in-cluster config. Inject ResourceRef or observations.extra["target_ref"].
bundle = k8s_observation_providers()  # or client=FakeKubernetesClient(...) in tests
out = GRAPH.invoke(state, providers=bundle)
```

The provider translates pods/events/logs into `Observations(logs=..., events=..., extra=...)`
— the reasoning workflow does not know the source is Kubernetes.

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
