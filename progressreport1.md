# Progress Report 1 — Autonomous Incident-Response Agent

**Report date:** 18 July 2026  
**Package:** `incident-response-agent` v0.1.0  
**Current stage:** Stage 0 — skeleton, synthetic CrashLoopBackOff dataset, deterministic agent lifecycle  
**Active branch:** `IncidentState` (ahead of `main` by the pipeline/LangGraph enhancements commit)  
**Working tree:** clean (all work committed)

This document captures architecture, decisions, git history, what is implemented, what is stubbed, and what comes next — based on the repository state and local commit history through `0d453ae`.

---

## 1. Executive summary

We are building an **autonomous Kubernetes incident-response agent** (LangGraph-oriented) that will eventually:

1. Ingest alerts and observations  
2. Diagnose symptoms  
3. Hypothesize root causes  
4. Plan remediations  
5. Validate plans  
6. Score confidence  
7. (Later) Approve and execute fixes  

**Stage 0 focus is not a live cluster agent.** It establishes:

- Versioned **Pydantic contracts** for the full lifecycle  
- A **reproducible synthetic CrashLoopBackOff dataset** (JSONL) for eval/benchmarks  
- Pure, deterministic **decision nodes** with no LLM and no cluster I/O  
- Two orchestration paths that must stay equivalent: plain pipeline + LangGraph  
- Benchmark baselines (oracle / empty / heuristic) to prove plumbing  

External progressive plan referenced by the project README (not checked into this repo):

`C:\Users\ag551\.claude\plans\https-github-com-madhurprash-langgraph-a-velvety-peacock.md`

---

## 2. Local change history (git)

### 2.1 Commits to date

| Commit | Date | Summary |
|--------|------|---------|
| `914d877` | 2026-06-15 | **Add project skeleton** — packages, contracts, CrashLoop dataset generator, eval, benchmark, rule-based nodes, initial tests (+2168 lines) |
| `0d453ae` | 2026-06-21 | **Add run_pipeline + enhance incident handling** — LangGraph graph, deterministic pipeline, fixtures JSON, expanded categories/metrics, confidence engine, parity tests (+1527 / −291 lines) |

### 2.2 Commit 1 — Project skeleton (`914d877`)

Established the foundation:

- Packaging: `pyproject.toml` (Python ≥3.12, Pydantic v2, optional LangGraph extra, pytest/ruff)  
- Contracts: `IncidentState` and related models under `src/incident_agent/contracts/`  
- Dataset: CrashLoopBackOff schema + generator + JSONL loaders + eval CLI  
- Nodes: diagnose, hypothesize, plan_fix, validate_fix, score_confidence  
- Remediation catalog + structural validation engine  
- Benchmark runner with oracle / empty / heuristic baselines  
- Initial interface/unit tests for dataset, nodes, validation, and benchmark  

### 2.3 Commit 2 — Pipeline & LangGraph (`0d453ae`) — *current branch tip vs `main`*

Moved from “nodes + dataset” to a runnable end-to-end lifecycle:

- `run_pipeline.py` — CLI over dataset → `GRAPH.invoke` → print diagnosis / hypotheses / plan / validation / confidence  
- `pipeline.py` — `run_deterministic_lifecycle(...)` without LangGraph  
- `graph.py` — straight-line LangGraph `StateGraph` with `IncidentStateGraph` wrapper  
- `confidence_engine.py` — baseline confidence used by the main flow  
- Fixture file `fixtures/crashloop_fixtures.json` — template-driven generation  
- Expanded CrashLoop categories (image / app / resource / dependency) + legacy aliases  
- Richer eval metrics (hypothesis top-k, fix-kind accuracy, validation pass rate, confidence ECE)  
- Tests for fixtures, deterministic lifecycle, LangGraph↔pipeline parity, expanded metrics  

### 2.4 Branch posture

- `main` contains the skeleton commit.  
- `IncidentState` contains skeleton + pipeline/LangGraph enhancements.  
- No uncommitted local changes at report time.

---

## 3. Goals and progressive build strategy

### 3.1 Why progressive / Stage 0

| Decision | Rationale |
|----------|-----------|
| Build in stages | Avoid premature complexity (LLM, K8s clients, approval loops) before contracts and eval exist |
| CrashLoopBackOff first | Common, high-signal K8s failure class with clear logs/events and fix patterns |
| Synthetic data before live clusters | Reproducible eval, no cluster credentials, fast iteration |
| Deterministic before AI | Prove interfaces and metrics; LLM/tools plug in later behind the same contracts |
| Dual orchestration | LangGraph for future branching/checkpointing; plain pipeline for simple tests and debugging |

### 3.2 Intended full lifecycle (contract phases)

```
ingest → diagnose → hypothesize → plan_fix → validate_fix
       → score_confidence → approve → execute → done
```

**Wired and exercised today:** through confidence / `done`.  
**Reserved in schema only:** `approve`, `execute` (plus `Approval`, `ExecutionResult` models).

---

## 4. Architecture overview

### 4.1 Repository layout

```
autonomous incident-response agent/
├── README.md                          # Stage-0 quickstart
├── progressreport1.md                 # This report
├── pyproject.toml                     # Package + deps + ruff/pytest
├── run_pipeline.py                    # CLI: GRAPH over dataset incidents
├── src/incident_agent/
│   ├── contracts/                     # Shared typed state contracts
│   ├── nodes/                         # Pure decision functions (state → partial updates)
│   ├── remediation/                   # Rule-based fix catalog
│   ├── validation/                    # Structural validation engine
│   ├── datasets/                      # Loaders, CrashLoop generator, eval metrics
│   ├── pipeline.py                    # Deterministic lifecycle (no LangGraph)
│   ├── graph.py                       # LangGraph straight-line StateGraph
│   └── benchmark.py                   # Baseline predictors + artifact writer
└── tests/                             # Interface, unit, integration (parity) tests
```

Generated at runtime (gitignored):

- `data/synthetic/.../*.jsonl` — dataset output  
- `artifacts/` — benchmark predictions and reports  
- `.venv/`, `.env`  

**Not present yet:** `tools/`, live integrations (K8s/Prometheus/LLM), ADR directory, settings wiring.

### 4.2 Layered design

```
┌─────────────────────────────────────────────────────────────┐
│  CLI / entrypoints                                          │
│  run_pipeline.py · benchmark CLI · dataset generate/eval CLIs │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  Orchestration                                               │
│  graph.py (LangGraph)  ≅  pipeline.py (deterministic)        │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  Decision nodes (pure functions)                             │
│  diagnose · hypothesize · plan_fix · validate_fix · confidence │
└──────────────┬─────────────────────────────┬────────────────┘
               │                             │
┌──────────────▼──────────┐    ┌─────────────▼────────────────┐
│  remediation/catalog.py │    │  validation/engine.py         │
│  hypothesis → FixPlan   │    │  FixPlan → ValidationVerdict  │
└─────────────────────────┘    └──────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  contracts/models.py — IncidentState + Alert, Diagnosis, …   │
└─────────────────────────────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  datasets/ — synthetic CrashLoop JSONL + eval + benchmarks   │
└─────────────────────────────────────────────────────────────┘
```

### 4.3 Core contract: `IncidentState`

Defined in `src/incident_agent/contracts/models.py`.

Design goals encoded in the model docstring:

1. **One contract** for the whole pipeline  
2. **Deterministic-first** — nodes run without external systems  
3. **Backward-compatible** — new fields optional or safely defaulted  

Important types:

| Type | Role |
|------|------|
| `Alert` | Normalized alert (name, severity, labels, annotations, optional raw) |
| `Observations` | `logs`, `events`, `extra` (runtime knobs like `top_n`, `target_ref`) |
| `Diagnosis` / `Hypothesis` / `Evidence` | Reasoning payloads |
| `FixAction` / `FixPlan` / `RiskLevel` | Remediation plan |
| `ValidationResult` / `ValidationVerdict` | Validation outputs |
| `ConfidenceScore` | Score + explanation |
| `Approval` / `ExecutionResult` | Future phases |
| `ResourceRef` | Source-agnostic K8s pointer (no client types) |
| `IncidentState` | Top-level mutable pipeline state |

**Contract hardening decisions:**

- `ContractBase`: `extra="forbid"` — prevent silent schema drift  
- `validate_assignment=True` — catch bad mutations early  
- `schema_version: "1"` on contracts/predictions for forward evolution  

### 4.4 Node contract

Every node:

- **Accepts** `IncidentState`  
- **Returns** `dict[str, object]` partial updates (LangGraph-friendly)  
- **Must not** require LLM or cluster I/O in Stage 0  

| Node | Module | Behavior today |
|------|--------|----------------|
| Ingest | `graph.py` / pipeline setup | Ensure `observations.extra` defaults (`top_n`, `target_ref`) |
| Diagnose | `nodes/diagnose.py` | **Stub:** always `"Invalid image tag"` (interface proof) |
| Hypothesize | `nodes/hypothesize.py` | Keyword/regex scoring over ~13 causes; Top-N (≥2) with normalized likelihoods |
| Plan fix | `nodes/plan_fix.py` | Delegates to remediation catalog |
| Validate | `nodes/validate_fix.py` | Structural checks via `validate_plan()` |
| Confidence | `nodes/confidence_engine.py` | `top_hypothesis_p × (1 if validation passed else 0)` |

Note: `nodes/score_confidence.py` implements a richer heuristic (noop/risk penalties) but is **not** used by the main pipeline/graph path; `compute_confidence` is.

### 4.5 Orchestration (two equivalent paths)

```mermaid
flowchart LR
  START --> ingest --> diagnose --> hypothesize --> plan_fix --> validate_fix --> confidence --> END
```

**A. Deterministic pipeline** — `run_deterministic_lifecycle` in `pipeline.py`  
**B. LangGraph** — `build_graph()` / `GRAPH` in `graph.py`

Parity is enforced by `tests/test_langgraph_integration.py` (outputs match modulo `created_at`).

LangGraph is an **optional** extra (`pip install .[agent]`). Checkpoint SQLite is listed but **not wired**.

### 4.6 Remediation catalog

`remediation/catalog.py` maps hypothesis descriptions to planners (e.g. `missing_secret.v1`, `invalid_image.v1`). Highest-likelihood matching hypothesis wins; otherwise `NOOP`. Actions carry kubectl **hints** in `params` — never executed.

### 4.7 Validation engine

`validation/engine.py` is explicitly **interface-first / fake**:

- Checks risk, non-empty actions, supported `FixActionType`, target, rationale  
- Later: kubectl dry-run, staging replay, simulation (`ValidationResult.method` already anticipates these)

---

## 5. Dataset architecture (CrashLoopBackOff)

### 5.1 Why a separate dataset schema

Agent contracts (`contracts/models.py`) and dataset schema (`datasets/crashloopbackoff/schema.py`) are **related but separate**:

- Dataset focuses on ground-truth labels for eval (`category`, `root_cause`, `expected_fix`)  
- Agent state focuses on lifecycle payloads (`diagnosis`, `hypotheses`, `fix_plan`, …)  

`run_pipeline.py` maps dataset incidents → `IncidentState` at the boundary.

### 5.2 `CrashLoopBackOffIncident` fields

- Identity: `incident_id`, `created_at`, `incident_type`, `schema_version="1"`  
- Target: `K8sRef` (namespace, kind, name)  
- Signals: `alert`, `logs`, `events`  
- Labels: `category`, `root_cause`, `expected_fix` (`summary`, `kind`, `kubectl_hint`)  
- Optional: `distractors` (reserved for harder eval; currently empty)

### 5.3 Category taxonomy (v2-ish + legacy)

**Expanded (current fixtures):**

| Group | Categories |
|-------|------------|
| Invalid image | `invalid_image_wrong_tag`, `invalid_image_deleted_image`, `invalid_image_private_registry_auth` |
| Application | `missing_secret`, `missing_env_var`, `bad_config`, `startup_exception` |
| Resource | `oom`, `disk_pressure`, `cpu_starvation` |
| Dependency | `dependency_database_unavailable`, `dependency_redis_unavailable`, `dependency_dns_failure` |

**Legacy aliases** retained for older datasets/tests (e.g. `resource_constraint_oom`, `app_bug_unhandled_exception`).

### 5.4 Generation pipeline

1. Seeded RNG (`seed + idx`) → reproducible IDs  
2. Sample category → load template from `fixtures/crashloop_fixtures.json`  
3. Format placeholders (`{namespace}`, `{image}`, `{secret_name}`, …)  
4. Emit JSONL  

```powershell
python -m incident_agent.datasets.crashloopbackoff.generate `
  --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" --n 50 --seed 1337
```

### 5.5 Evaluation & benchmarks

**Eval** (`datasets/eval.py`) metrics include:

- Category accuracy  
- Diagnosis / root-cause accuracy  
- Hypothesis top-1 / top-3  
- Fix-kind accuracy  
- Validation pass rate  
- Average confidence  
- Confidence ECE (vs fix correctness)

**Benchmark baselines** (`benchmark.py`):

| Baseline | Purpose |
|----------|---------|
| `oracle` | Copy ground truth — sanity (expect ~1.0 category accuracy) |
| `empty` | No predictions — lower bound |
| `heuristic` | Keyword matcher — end-to-end plumbing without AI |

Artifacts: `predictions.<baseline>.jsonl`, `report.<baseline>.json` under `artifacts/`.

---

## 6. Architectural decisions (decision log)

These decisions are visible in README, module docstrings, and commit evolution. There is no formal ADR folder yet.

| # | Decision | Why |
|---|----------|-----|
| D1 | **Contracts before tooling/AI** | Stable interfaces so LLM/K8s clients can swap without rewriting the pipeline |
| D2 | **`extra="forbid"` on contracts** | Fail loud on schema drift |
| D3 | **Deterministic Stage 0** | Measurable progress without API keys or clusters |
| D4 | **Diagnose stubbed on purpose** | Prove node shape first; real log/event diagnose comes later |
| D5 | **Validation interface-first** | Structural pass/fail now; dry-run/staging later without changing call sites that only need verdict |
| D6 | **Config-in-state** (`observations.extra`) | Avoid Settings object until needed; nodes stay portable |
| D7 | **Dual orchestration + parity test** | LangGraph-ready without locking tests to LangGraph |
| D8 | **Synthetic seeded JSONL + fixtures** | Reproducible datasets; templates separate from generation logic |
| D9 | **Expand categories without bumping `schema_version` yet** | Keep `"1"`; add v2-ish categories + legacy aliases for compatibility |
| D10 | **Benchmark baselines before real agent** | Prove eval harness before investing in LLM accuracy |
| D11 | **Source-agnostic `ResourceRef`** | No Kubernetes client types in core contracts |
| D12 | **Remediation as kubectl hints, not execution** | Safe Stage 0; execute phase reserved |
| D13 | **Reserve replan fields** (`replan_count` / `max_replans`) | Future LangGraph loops without contract churn |
| D14 | **Optional `[agent]` extra** | Core package stays light; LangGraph opt-in |
| D15 | **Interface tests over live integration tests** | Fast CI; no cluster/LLM flakiness |

---

## 7. Tech stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Language | Python ≥3.12 | Enforced in `pyproject.toml` |
| Schemas | Pydantic ≥2 | Primary modeling layer |
| Orchestration | LangGraph ≥0.2 (optional) | Straight-line graph only |
| Packaging | PEP 621 / editable install | `pip install -e ".[dev]"` / `".[agent]"` |
| Tests | pytest ≥8 | Quiet mode (`-q`) |
| Lint | ruff ≥0.5 | line-length 100, py312 |
| Listed but unused so far | `pydantic-settings`, `python-dotenv`, `httpx`, `rich`, checkpoint SQLite | Reserved for later stages |

---

## 8. Testing posture

**Style:** contract/interface tests, determinism checks, dataset round-trips, graph↔pipeline parity.  
**Not covered yet:** live cluster, real kubectl dry-run, LLM calls.

| Test file | Focus |
|-----------|--------|
| `test_dataset_core_loader.py` | Strict/permissive JSONL loading |
| `test_crashloop_dataset.py` | Generate + validate + write |
| `test_crashloop_fixtures.py` | Fixtures JSON + `make_incident` |
| `test_dataset_eval_crashloop.py` | Category accuracy / missing preds |
| `test_eval_metrics.py` | Expanded metrics (hypotheses, fix, ECE) |
| `test_benchmark_runner.py` | Oracle baseline ≈ 1.0 accuracy |
| `test_diagnose_node_interface.py` | Stable hardcoded diagnose output |
| `test_hypothesize_node_interface.py` | Top-N; never a single hypothesis |
| `test_planning_validation_confidence.py` | Plan/validate/score determinism |
| `test_validation_engine_interface.py` / `test_contract_validation_engine.py` | `validate_plan` pass/fail |
| `test_deterministic_lifecycle.py` | Full pipeline run |
| `test_langgraph_integration.py` | GRAPH ≡ pipeline (modulo timestamps) |

---

## 9. How to run (current Stage 0)

```powershell
cd "d:\autonomous incident-response agent"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e ".[dev,agent]"

# Dataset
python -m incident_agent.datasets.crashloopbackoff.generate `
  --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" --n 50

# Deterministic / LangGraph demo
python run_pipeline.py --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" --limit 3

# Benchmark plumbing
$env:PYTHONPATH=(Resolve-Path .\src).Path
py -m incident_agent.benchmark --task crashloopbackoff `
  --dataset ".\data\synthetic\crashloopbackoff\incidents.jsonl" `
  --baseline heuristic --out-dir ".\artifacts\benchmarks\crashloop"

# Tests
pytest
```

---

## 10. Implemented vs stubbed

### 10.1 Implemented (working)

- [x] Project packaging and Stage-0 README  
- [x] CrashLoopBackOff schema, fixtures, seeded generator CLI  
- [x] JSONL load/write + evaluation CLI + metrics (incl. ECE)  
- [x] Benchmark runner (oracle / empty / heuristic)  
- [x] Keyword/regex hypothesize engine (Top-N)  
- [x] Remediation catalog + rule-based planning  
- [x] Structural validation engine  
- [x] Baseline confidence (`compute_confidence`)  
- [x] Deterministic lifecycle + LangGraph straight-line graph  
- [x] Graph↔pipeline parity tests  
- [x] Shared `IncidentState` including future approve/execute fields  
- [x] `run_pipeline.py` demo CLI  

### 10.2 Stubbed / reserved / unused

| Item | Status |
|------|--------|
| `diagnose` | Always returns `"Invalid image tag"` |
| Validation | Structural only — no dry-run / staging / simulation |
| Approve / execute phases | In schema; no nodes or edges |
| `score_confidence` richer heuristic | Exists but not on main path |
| Replanning loops | Fields exist; no graph edges |
| Distractors | Field reserved; generator leaves `[]` |
| LLM / tool-calling agent | Not present |
| Live K8s / Prometheus / httpx | Not integrated |
| Settings / dotenv | Dependencies unused |
| LangGraph checkpointing | Optional package not configured |
| Formal ADRs | Architecture lives in comments + this report |

---

## 11. Progress against Stage 0 exit criteria

| Criterion | Status |
|-----------|--------|
| Installable Python package with clear layout | Done |
| Versioned agent contracts (`IncidentState`) | Done |
| First incident class dataset (CrashLoop) | Done |
| Deterministic lifecycle end-to-end | Done |
| Eval + benchmark harness | Done |
| LangGraph wiring with parity | Done |
| Real diagnose from logs/events | Not started (stub) |
| Meaningful validation beyond structure | Not started |
| Human approval / execution | Not started |
| LLM-backed nodes | Not started |
| Live cluster tools | Not started |

**Verdict:** Stage 0 skeleton goals are largely met. The project is ready for Stage 1 work that replaces stubs with real diagnosis and richer validation while keeping the same contracts.

---

## 12. Recommended next stages (implied roadmap)

Ordered by dependency on existing contracts:

1. **Real diagnose node** — derive summary/confidence/evidence from logs/events (retire hardcoded diagnose)  
2. **Align hypothesize slugs ↔ dataset categories** — tighten eval accuracy of the heuristic agent  
3. **Populate distractors** — harder negative signal for robustness eval  
4. **Richer validation** — kubectl dry-run / staging / simulation behind `ValidationVerdict`  
5. **Confidence unification** — pick one confidence node (`compute_confidence` vs `score_confidence`)  
6. **LangGraph branching** — fail-validation → replan loop using `replan_count` / `max_replans`  
7. **Approve + execute** — human-in-the-loop gates; still no blind prod mutation  
8. **LLM-backed nodes** — same interfaces; deterministic nodes remain baselines  
9. **Live integrations** — K8s API, Prometheus/logs backends, Settings/env wiring  
10. **More incident classes** — beyond CrashLoopBackOff  
11. **Formal ADRs** — promote this decision log into `docs/adr/` if the team wants permanence  

---

## 13. Key symbol index

| Symbol | Path |
|--------|------|
| `IncidentState` | `src/incident_agent/contracts/models.py` |
| `run_deterministic_lifecycle` | `src/incident_agent/pipeline.py` |
| `GRAPH` / `build_graph` | `src/incident_agent/graph.py` |
| `CrashLoopBackOffIncident` | `src/incident_agent/datasets/crashloopbackoff/schema.py` |
| `make_incident` / generate CLI | `src/incident_agent/datasets/crashloopbackoff/generate.py` |
| `evaluate_crashloop` | `src/incident_agent/datasets/eval.py` |
| `plan_from_hypotheses` | `src/incident_agent/remediation/catalog.py` |
| `validate_plan` | `src/incident_agent/validation/engine.py` |
| `compute_confidence` | `src/incident_agent/nodes/confidence_engine.py` |
| `run_crashloop_benchmark` | `src/incident_agent/benchmark.py` |
| Demo CLI | `run_pipeline.py` |

---

## 14. Bottom line

Through commits `914d877` → `0d453ae` on branch `IncidentState`, the project has a **contract-first, deterministic baseline** for a LangGraph incident agent:

- Evaluation harness and rule-based lifecycle are real  
- Dataset + fixtures + benchmarks are in place  
- Diagnosis, external validation, approval, execution, and AI/cluster integrations remain intentional Stage-0 stubs  

That is the complete architectural and decision state as of Progress Report 1.
