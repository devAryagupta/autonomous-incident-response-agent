# Autonomous Incident-Response Agent

Progressive build of a **LangGraph-oriented Kubernetes incident-response agent**.

External stage plan (not in this repo):
`C:\Users\ag551\.claude\plans\https-github-com-madhurprash-langgraph-a-velvety-peacock.md`

**Package:** `incident-response-agent` v0.1.0 · **Python:** ≥3.12 · **Stage:** 0

---

## What this repo is today

A **deterministic, no-LLM Stage 0** system that:

1. Loads **synthetic CrashLoopBackOff** incidents (JSONL)
2. Runs a full lifecycle: enrich → diagnose → hypothesize → plan → validate → confidence
3. **Replans** when confidence is low (until `max_replans`)
4. **Dry-runs** the fix plan via an `ExecutionProvider` (no live cluster)
5. Supports the same flow via **LangGraph** (`graph.py`) or a plain **pipeline** (`pipeline.py`)
6. Evaluates / benchmarks predictions against dataset ground truth

Nodes stay source-agnostic through a **provider architecture**. Stage-0 backends are synthetic / dry-run / no-memory; later swaps (K8s, Prometheus, Kubectl, Chroma) should not require rewriting business nodes.

---

## Contributing & standards

| Doc | Purpose |
|-----|---------|
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, checks, where to put new code, PRs |
| [docs/CODING_PRINCIPLES.md](docs/CODING_PRINCIPLES.md) | Layers, naming, size limits, SoC, runtime vs source |
| [docs/README.md](docs/README.md) | Docs index |
| [runtime/README.md](runtime/README.md) | Runtime data layout (outside `src/`) |
| `.cursor/rules/` | Cursor agent rules (architecture + Python style) |

`progressreport1.md` is a **historical** snapshot from an earlier Stage-0 cut (before providers + replan). Prefer **this README** for the current codebase.

---

## Architecture (current)

```
START → enrich → diagnose → hypothesize → plan_fix → validate_fix → confidence
                                                      ├─ high score / max replans → finalize → END
                                                      └─ low score + retries left → replan → hypothesize ↺
```

| Layer | Location | Role |
|-------|----------|------|
| Contracts | `src/incident_agent/contracts/` | `IncidentState` and related Pydantic models |
| Business nodes | `src/incident_agent/nodes/` | `enrich`, `diagnose`, `hypothesize`, `plan_fix`, `validate_fix`, confidence |
| Remediation | `src/incident_agent/remediation/` | Rule catalog: hypothesis → `FixPlan` |
| Validation | `src/incident_agent/validation/` | Structural `validate_plan` (interface-first) |
| Orchestration | `graph.py`, `pipeline.py`, `routing.py` | LangGraph app, deterministic lifecycle, confidence router / finalize |
| Providers | `src/incident_agent/providers/` | Observation / Metrics / Execution / Memory protocols + Stage-0 backends |
| Datasets | `src/incident_agent/datasets/` | CrashLoop generator, loaders, eval metrics |
| Demo CLI | `run_pipeline.py` | Run `GRAPH` over a dataset and print results |
| Benchmark | `benchmark.py` | Oracle / empty / heuristic baselines → artifacts |
| Runtime data | `runtime/` | Logs, memory dumps, state, ad-hoc artifacts (**not** under `src/`) |
| Generated data | `data/`, `artifacts/` | Synthetic JSONL / benchmark reports (gitignored) |

**Not present yet:** `prompts/`, `llm/`, `tools/`, live K8s/Prometheus, human approve UI, real execute.

### Provider injection

```text
GRAPH.invoke(state)  # uses default_providers()
# or
GRAPH.invoke(state, providers=ProviderBundle(...))
# LangGraph config key: configurable["providers"]
```

Stage-0 defaults: `SyntheticObservationProvider`, `SyntheticMetricsProvider`, `DryRunExecutionProvider`, `NoMemoryProvider`.

### Routing knobs

| Knob | Default | Where |
|------|---------|--------|
| Confidence threshold | `0.7` | `observations.extra["confidence_threshold"]` or `DEFAULT_CONFIDENCE_THRESHOLD` |
| Max replans | `3` | `IncidentState.max_replans` |
| Top-N hypotheses | `3` | `observations.extra["top_n"]` |

---

## Implemented vs stubbed

| Piece | Status |
|-------|--------|
| CrashLoop schema, fixtures, seeded generator | Implemented |
| JSONL load/eval + ECE / hypothesis / fix metrics | Implemented |
| Benchmark baselines (oracle / empty / heuristic) | Implemented |
| Enrich via providers | Implemented |
| Keyword/regex hypothesize + remediation catalog | Implemented |
| Structural validation | Implemented |
| Confidence + replan loop (graph + pipeline) | Implemented |
| Finalize dry-run execution | Implemented |
| Graph ↔ pipeline parity tests | Implemented |
| **Diagnose** | **Stub** — always `"Invalid image tag"` |
| Validation dry-run / staging / simulation | Not built (method reserved on contracts) |
| Approve / live execute | Schema only |
| LLM / prompts / live tools | Not built |
| Distractors on generated incidents | Field exists; generator leaves `[]` |
| LangGraph SQLite checkpointing | Optional dep; not wired |

---

## Package layout

```
src/incident_agent/
  contracts/          # IncidentState, Alert, FixPlan, …
  nodes/              # enrich, diagnose, hypothesize, plan, validate, confidence
  remediation/        # fix catalog
  validation/         # structural validation engine
  providers/          # protocols + synthetic / dry_run / memory stubs
  datasets/
    crashloopbackoff/ # schema, fixtures, generate CLI
    core.py           # JSONL load/write
    eval.py           # evaluation CLI
  graph.py            # LangGraph StateGraph + GRAPH wrapper
  pipeline.py         # run_deterministic_lifecycle
  routing.py          # route_on_confidence, bump_replan, finalize
  benchmark.py        # baseline runner
run_pipeline.py       # demo over dataset
tests/                # interface, lifecycle, providers, routing, eval
runtime/              # runtime data placeholders
docs/                 # coding principles
```

---

## Quickstart

### Create venv + install

```powershell
cd "d:\autonomous incident-response agent"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev,agent]"
```

- Core deps include **LangGraph** (required for `graph.py` / `run_pipeline.py`).
- `[agent]` adds optional `langgraph-checkpoint-sqlite` (not wired yet).
- `[dev]` adds pytest + ruff.

### Generate CrashLoop dataset

```powershell
python -m incident_agent.datasets.crashloopbackoff.generate `
  --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" --n 50 --seed 1337
```

Details: [datasets/crashloopbackoff/README.md](src/incident_agent/datasets/crashloopbackoff/README.md)

### Run the agent over the dataset (LangGraph)

```powershell
python run_pipeline.py --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" --limit 3
```

Prints diagnosis, hypotheses, fix plan, validation, and confidence per incident.

### Evaluate predictions

Predictions JSONL (`CrashLoopPrediction`), one object per `incident_id`:

```json
{
  "schema_version": "1",
  "incident_id": "clb-...",
  "predicted_category": "missing_env_var",
  "predicted_root_cause": "...",
  "predicted_fix_summary": "...",
  "predicted_hypotheses": [{"cause": "...", "likelihood": 0.4}],
  "predicted_fix_kind": "patch_config",
  "predicted_validation_passed": true,
  "predicted_confidence_score": 0.72
}
```

```powershell
python -m incident_agent.datasets.eval `
  --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" `
  --predictions ".\predictions.jsonl"
```

Metrics include category accuracy, hypothesis top-1/top-3, fix-kind accuracy, diagnosis accuracy, validation pass rate, average confidence, and confidence ECE.

### Benchmark baselines

Writes `predictions.<baseline>.jsonl` and `report.<baseline>.json`:

```powershell
python -m incident_agent.benchmark `
  --task crashloopbackoff `
  --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" `
  --baseline heuristic `
  --out-dir ".\artifacts\benchmarks\crashloop"
```

| Baseline | Purpose |
|----------|---------|
| `oracle` | Copy ground truth (expect ~1.0 category accuracy) |
| `empty` | No predictions (lower bound) |
| `heuristic` | Keyword matcher — plumbing check, no AI |

### Tests & lint

```powershell
pytest
ruff check src tests
```

---

## CrashLoop categories (generator fixtures)

Invalid image · application · resource · dependency (plus legacy aliases for older datasets):

`invalid_image_wrong_tag`, `invalid_image_deleted_image`, `invalid_image_private_registry_auth`,  
`missing_secret`, `missing_env_var`, `bad_config`, `startup_exception`,  
`oom`, `disk_pressure`, `cpu_starvation`,  
`dependency_database_unavailable`, `dependency_redis_unavailable`, `dependency_dns_failure`

Templates live in `datasets/crashloopbackoff/fixtures/crashloop_fixtures.json`.

---

## Tech stack

| Piece | Choice |
|-------|--------|
| Language | Python ≥3.12 |
| Schemas | Pydantic v2 (`extra="forbid"` on contracts) |
| Orchestration | LangGraph ≥0.2 |
| HTTP / settings / dotenv / rich | Listed; little or unused in Stage 0 |
| Lint / test | Ruff (line length 100), pytest |

---

## Suggested next work

1. Replace hardcoded **diagnose** with log/event-driven diagnosis  
2. Align hypothesize slugs with dataset categories for stronger heuristic scores  
3. Richer validation (kubectl dry-run / staging) behind the same verdict interface  
4. Wire checkpointing / approve phase when ready  
5. Add `prompts/` + `llm/` + `tools/` packages when LLM/tools land (see coding principles)  
6. Optional physical rename `nodes/` → `business/`, orchestration into a package (documented target layout)
