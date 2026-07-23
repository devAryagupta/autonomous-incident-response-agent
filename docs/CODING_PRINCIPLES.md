# Coding Principles & Patterns

This document is the **source of truth** for how we structure and write code in this repository.
All contributors (human and AI) must follow these rules.

Related files:

- [CONTRIBUTING.md](../CONTRIBUTING.md) — how to set up, test, and submit changes
- [.cursor/rules/](../.cursor/rules/) — Cursor agent rules that enforce the same standards

---

## 1. Goals

We are building an autonomous incident-response agent **progressively**. The codebase must stay:

1. **Easy to map** — a decision tree a model (or new contributor) can follow without deep archaeology
2. **Separated by concern** — prompts, LLM calls, orchestration, tools, and business logic stay isolated
3. **Self-documenting** — names explain intent; no cryptic abbreviations or dump folders
4. **Source vs runtime clean** — logs, memory dumps, and agent state never live under `src/`

---

## 2. Layered architecture (separation of concerns)

Depend inward. Outer layers may call inner layers; never the reverse for domain rules.

```
prompts  →  llm  →  orchestration  →  business  →  contracts
                         ↕
                   providers / tools
                         ↕
              runtime/ + data/  (outside src/)
```

| Layer | Responsibility | Must not contain |
|-------|----------------|------------------|
| `contracts/` | Pydantic models / typed state (`IncidentState`, etc.) | Business rules, I/O, prompts |
| `business/` (today: `nodes/`, `remediation/`, `validation/`) | Domain decisions: diagnose, hypothesize, plan, validate, score | LangGraph wiring, HTTP, LLM APIs, prompt text |
| `orchestration/` (today: `graph.py`, `pipeline.py`, `routing.py`) | Graph edges, replan loop, pipeline sequencing | Root-cause heuristics, prompt templates |
| `providers/` | Observation / Metrics / Execution / Memory adapters | Scoring formulas, hypothesis catalogs |
| `tools/` *(future)* | Kubectl, HTTP, cluster helpers as callable tools | Business scoring or graph topology |
| `llm/` *(future)* | Model clients, token / retry wrappers | Domain “what is the root cause?” logic |
| `prompts/` *(future)* | Prompt templates / message builders only | `invoke()`, API keys, domain algorithms |
| `datasets/` | Synthetic schemas, generators, eval metrics | Live cluster I/O |
| `cli/` *(target)* | Entry points (run, benchmark, generate) | Deep business logic |

**Current Stage-0 map** (names will migrate toward the target folders above without changing these boundaries):

| Concern | Location today |
|---------|----------------|
| Contracts | `src/incident_agent/contracts/` |
| Business nodes | `src/incident_agent/nodes/` |
| Remediation rules | `src/incident_agent/remediation/` |
| Validation rules | `src/incident_agent/validation/` |
| Orchestration | `graph.py`, `pipeline.py`, `routing.py` |
| Providers | `src/incident_agent/providers/` |
| Datasets | `src/incident_agent/datasets/` |

When adding LLM / tools / prompts, create the dedicated packages — do **not** drop prompt strings into `nodes/` or business logic into `graph.py`.

---

## 3. Flat over deep

- Prefer **flat packages** with descriptive module names over deep trees.
- Maximum nesting under `src/incident_agent/`: **3–4 levels** (including the module file).
- Good: `datasets/crashloopbackoff/schema.py`
- Bad: `datasets/crashloop/backoff/v1/schemas/models/types.py`
- Do **not** create `utils/`, `helpers/`, `common/`, `misc/`, or `shared/` grab-bags.
  Put code in a package named after the **capability** it provides.

---

## 4. Naming conventions (Python / PEP 8)

| Kind | Convention | Examples |
|------|------------|----------|
| Modules / files | `snake_case` | `plan_fix.py`, `confidence_routing.py` |
| Functions / methods / variables | `snake_case` | `route_on_confidence`, `fetch_observations` |
| Classes | `PascalCase` | `IncidentState`, `ProviderBundle` |
| Module-level constants / globals | `UPPER_SNAKE_CASE` | `DEFAULT_CONFIDENCE_THRESHOLD`, `PROVIDERS_CONFIG_KEY` |
| Booleans | Prefix with `is_`, `has_`, `can_`, `should_` | `is_validation_passed`, `has_retries_left` |
| Packages | short `snake_case`, capability-named | `providers`, `remediation` — not `util` |

### Self-documenting names

```python
# Bad
def run(x): ...
def process_data(d): ...
HELPERS = []

# Good
def route_on_confidence(state: IncidentState) -> RouteDecision: ...
def plan_from_hypotheses(*, hypotheses: list[Hypothesis], target_ref: str) -> FixPlan: ...
DEFAULT_CONFIDENCE_THRESHOLD = 0.7
```

Enums and action types use clear values (`restart_pod`, not `rp`).

---

## 5. Source vs runtime data

**Never** write agent runtime artifacts into `src/`.

| Path | Purpose |
|------|---------|
| `src/incident_agent/` | Product source only |
| `runtime/logs/` | Run logs |
| `runtime/memory/` | Memory / retrieval dumps |
| `runtime/state/` | Checkpoints / persisted incident state |
| `runtime/artifacts/` | Ad-hoc run outputs |
| `data/` | Generated datasets (e.g. synthetic JSONL) |
| `artifacts/` | Benchmark reports / predictions (existing) |

These directories are gitignored except for placeholder `.gitkeep` / README files that document intent.

Providers (`MemoryProvider`, etc.) define **interfaces**; persisted files belong under `runtime/`, not next to Python modules.

---

## 6. Function & module size

| Rule | Guidance |
|------|----------|
| Line length | **100** characters (enforced by Ruff) |
| Function body | Prefer **≤ ~40 lines**; refactor if approaching **~60** |
| Nesting inside functions | Prefer early returns over deep `if` pyramids |
| Single responsibility | One reason to change per function / module |
| Dead code | Delete unused functions, duplicate scorers, and commented-out blocks — do not leave “just in case” |

When a function grows:

1. Extract a **named** sub-function that describes the step  
2. Keep the parent as a short orchestrator  
3. Do not invent a `utils.py` for the leftover

---

## 7. Node / business function pattern

Decision steps accept state and return **partial updates** (dict), so LangGraph and the deterministic pipeline stay interchangeable:

```python
def hypothesize(state: IncidentState) -> dict[str, object]:
    ...
    return {"hypotheses": hypotheses, "chosen_hypothesis_id": top_id}
```

- No LLM calls inside business modules (until an explicit `llm/` boundary exists and is called from orchestration).
- No live cluster I/O inside business modules — go through `providers/` or `tools/`.
- Keep Stage-0 deterministic paths testable without network or API keys.

---

## 8. Provider pattern

Nodes and the graph depend on **Protocols** (`ObservationProvider`, `MetricsProvider`, `ExecutionProvider`, `MemoryProvider`), not concrete backends.

- Stage 0: Synthetic + DryRun + NoMemory  
- Live observations (opt-in): `KubernetesObservationProvider` via `k8s_observation_providers()`  
- Live metrics (opt-in): `PrometheusMetricsProvider` via `prometheus_metrics_providers()`  
- Calibration: `ConfidenceCalibrator` bounds overconfidence before mutation (see `calibration/`)  
- Execution: allowlisted `KubectlExecutionProvider` + `ExecutionPolicy` (see `execution/`)  
- Later: Chroma — **swap backends without rewriting business nodes**

Inject via LangGraph config / `ProviderBundle`; do not hard-code clients inside `diagnose` / `hypothesize`.

---

## 9. Testing patterns

- Prefer **interface and determinism tests** over live cluster / LLM tests.
- Graph and deterministic pipeline outputs should stay equivalent where both paths exist (parity tests).
- New business rules need a focused test; new providers need a fake/stub implementation for Stage 0.
- Run: `pytest` (and `ruff check` when touching Python).

---

## 10. What to do when adding a feature

1. Identify the **layer** (contract vs business vs orchestration vs provider vs prompt/llm/tool).  
2. Put the file in that layer with a **descriptive** name.  
3. Keep the public function small; extract helpers as named siblings in the same module if needed.  
4. Do not deepen the package tree past 3–4 levels.  
5. Do not write runtime files under `src/`.  
6. Add or update tests.  
7. Delete dead code you replace.

---

## 11. Anti-patterns (do not)

- Mixing prompt text, `ChatOpenAI.invoke`, and root-cause regexes in one file  
- `utils.py` / `helpers.py` catch-all modules  
- Generic names: `data`, `manager`, `handler`, `processor`, `stuff`  
- Storing checkpoints or logs under `src/incident_agent/`  
- Copy-pasting a second confidence scorer “for later” without deleting the unused one  
- Silent `except:` / swallowing errors that hide contract violations  

---

## 12. Target package sketch (evolutionary)

Folders marked *future* may be empty stubs with a docstring until needed. Prefer creating them when the first real file arrives, but **never** put that concern in the wrong layer.

```
src/incident_agent/
  contracts/          # typed models
  business/           # domain decisions (migrate from nodes/remediation/validation)
  orchestration/      # graph, pipeline, routing
  providers/          # I/O adapters
  tools/              # future
  llm/                # future
  prompts/            # future
  datasets/           # synthetic data + eval
  cli/                # future consolidation of entrypoints
runtime/              # logs, memory, state, artifacts (not source)
```

Until the physical rename lands, **follow the layer rules even if folder names still say `nodes/`**. Behavior and boundaries matter more than the rename; the rename should match this document when it happens.
