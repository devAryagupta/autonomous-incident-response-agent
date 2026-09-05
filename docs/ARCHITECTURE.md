# Architecture

This document explains **how the agent is structured** and **how one incident flows** through it. For coding rules, see [CODING_PRINCIPLES.md](CODING_PRINCIPLES.md). For shipped vs planned work, see [STATUS.md](STATUS.md).

---

## Design goals

1. **Deterministic core** — Stage-0 reasoning paths run without LLMs, API keys, or a cluster  
2. **Provider isolation** — Observation / Metrics / Execution / Memory swap without rewriting business nodes  
3. **Safety before autonomy** — a routing / gate score × risk policy gates every mutation  
4. **Eval-native** — synthetic incidents + decision traces + multi-dimensional scorecards  

Numbers in `[0, 1]` are not one probability. Hypothesis values are **belief** after
evidence updates; remediation scores are **heuristic suitability**, not `P(success)`.
See [SCORE_SEMANTICS.md](SCORE_SEMANTICS.md). Two belief-update
implementations exist; only the live stack is on the graph —
[VERIFICATION_STACKS.md](VERIFICATION_STACKS.md).

---

## Layered system

```mermaid
flowchart LR
  subgraph future ["Future"]
    P[prompts/]
    L[llm/]
    T[tools/]
  end

  subgraph today ["Stage 0"]
    ORCH[orchestration]
    BIZ[business]
    CON[contracts]
    PROV[providers]
  end

  P --> L --> ORCH
  ORCH --> BIZ --> CON
  ORCH <--> PROV
  T --> PROV
```

Depend **inward**. Domain heuristics must not import live clients.

| Layer | Today | Must not contain |
|-------|-------|------------------|
| Contracts | `contracts/` | I/O, prompts, scoring formulas used as side effects |
| Business | `nodes/`, `diagnosis/`, `hypothesis/`, `remediation/`, `validation/`, `verification/`, `evidence/`, `calibration/`, `execution/` (policy/actions) | LangGraph wiring, HTTP, LLM APIs |
| Orchestration | `graph.py`, `pipeline.py`, `routing.py` | Root-cause regexes, prompt templates |
| Providers | `providers/` | Hypothesis catalogs, confidence formulas |
| Datasets / eval | `datasets/`, `eval/`, `benchmark.py` | Live cluster mutation |
| Runtime | `runtime/`, `data/`, `artifacts/` | Product source |

Folder names may migrate toward `business/` and `orchestration/` packages; **boundaries matter more than the rename**.

---

## Provider model

Nodes and the graph depend on **protocols**, not concrete backends.

| Protocol | Stage-0 default | Opt-in live |
|----------|-----------------|-------------|
| `ObservationProvider` | Synthetic | `KubernetesObservationProvider` |
| `MetricsProvider` | Synthetic / stub | `PrometheusMetricsProvider` |
| `ExecutionProvider` | Dry-run | `KubectlExecutionProvider` (allowlisted) |
| `MemoryProvider` | No-op / local episode store | Extensible (e.g. vector store later) |

Inject via `ProviderBundle` on `GRAPH.invoke(...)`. Business nodes read `Observations` and write partial state updates — they do not know whether logs came from JSONL or the API server.

---

## Incident workflow

Compiled graph (`build_graph()` in `graph.py`):

```mermaid
flowchart TD
  START([START]) --> enrich
  enrich --> diagnose
  diagnose --> hypothesize
  hypothesize --> collect_evidence
  collect_evidence --> verify_hypotheses
  verify_hypotheses --> plan_fix
  plan_fix --> validate_fix
  validate_fix --> confidence

  confidence -->|execute| prepare_execution
  confidence -->|replan| replan
  confidence -->|escalate| escalate
  replan --> hypothesize
  escalate --> finalize

  prepare_execution --> pre_execute_validate
  pre_execute_validate --> approve
  approve --> execute
  execute --> verify_outcome
  verify_outcome --> finalize
  finalize --> END([END])
```

### Phase intent

| Phase | Intent |
|-------|--------|
| `enrich` | Attach provider-backed context to state |
| `diagnose` | One-shot **scope** classification (OOM / config / app). Not re-run on replan |
| `hypothesize` | Competing causes **inside that scope**, with normalized **prior belief** |
| `collect_evidence` | Planned telemetry pulls |
| `verify_hypotheses` | Live-stack posterior belief (`verification/engine.py`). Not the OOM metric loop |
| `plan_fix` | Rank actions by heuristic suitability + safety |
| `validate_fix` | Structural / policy checks on the plan |
| `pre_execute_validate` | Execution preconditions. DryRun infers target presence from state; kubectl provider GETs the resource |
| `confidence` | Routing / gate score (execute vs replan vs escalate) — not `P(success)` |
| `replan` | Increment counter; loop to **hypothesize**, never back to diagnose |
| `escalate` | Invalid diagnosis scope, or still low after max replans → NOOP / investigation; no mutation |
| `prepare_execution` … `execute` | Gate + allowlisted mutation (often dry-run) |
| `verify_outcome` | Four-layer outcome: command / service recovery / stability / root cause. Synthetic = regex evidence; Kubernetes = pod phase / ready / restarts / CrashLoopBackOff |
| `finalize` | Terminal state + memory episode |

### Confidence routing

The `confidence` node writes a **routing / gate score** (`ConfidenceScore` /
`IncidentState.confidence_score`). It is `top_belief × validation_pass` — not a
calibrated probability that the incident will resolve.

Diagnosis is performed **once** to establish incident scope. Replanning
operates within that scope — it re-ranks causes and plans, it does not
rebuild the category. There is no dynamic diagnostic graph.

Exception: if later evidence **no longer supports** the frozen family and
**does support** a different family, diagnosis is marked `scope_valid=false`
and the run escalates (`decision=NOOP`, `reason=DIAGNOSIS_SCOPE_INVALID`).
Mixed signals (original family still present) stay in the original scope.

From `routing.py` (defaults):

- Diagnosis scope contradicted → escalate (do not replan inside the wrong catalog)  
- Score ≥ threshold (default **0.7**) → enter execution lifecycle  
- Score low and `replan_count < max_replans` → replan  
- Still low after max replans → **NOOP** (`decision=NOOP`,
  `reason=INSUFFICIENT_CONFIDENCE`); execution is skipped

### Execution safety

`ExecutionPolicy` maps action `RiskLevel` → minimum **calibrated** confidence:

| Risk | Default minimum calibrated confidence |
|------|----------------------------------------|
| LOW | 0.75 |
| MEDIUM | 0.85 |
| HIGH | 0.95 |
| CRITICAL | 0.99 |

Allowlisted kubectl actions today: `restart_pod`, `rollback_deployment`,
`rollout_restart`, `scale_deployment`, `update_resource_limit`. These are
distinct operations — `rollback_deployment` is `rollout undo`, not a restart.
No free-form shell.

---

## Dual orchestration paths

| Path | Entry | Use |
|------|-------|-----|
| LangGraph | `GRAPH.invoke(state, providers=...)` | Branching, future checkpoints |
| Deterministic pipeline | `run_deterministic_lifecycle(...)` | Fast tests / debugging |

Parity tests should keep them aligned where both exist.

---

## Two Bayesian stacks (do not merge)

The word “Bayesian” appears twice. They are different programs.

| Stack | Runs on the incident graph? | Types |
|-------|-----------------------------|--------|
| **Live belief update** — `verification/engine.py` | Yes (`verify_hypotheses`) | `contracts.Hypothesis` |
| **OOM metric walkthrough** — `verification/loop.py` | No | `HypothesisState` |

The live stack uses regex specs and hand-set Bayes-factor **buckets**. The
loop uses a hand-set `P(E|H)` matrix for one evidence class (continuous
memory growth). Neither is a fitted or calibrated model of `P(cause)`.

Full roles and limits: [VERIFICATION_STACKS.md](VERIFICATION_STACKS.md).
Numbers: [SCORE_SEMANTICS.md](SCORE_SEMANTICS.md).

---

## Data & runtime layout

```text
src/incident_agent/     # product source only
runtime/                # logs, memory, state, ad-hoc artifacts (gitignored)
data/synthetic/         # generated JSONL datasets
artifacts/              # benchmark reports
```

Never write agent dumps under `src/`. See [runtime/README.md](../runtime/README.md).

---

## Evaluation architecture

`incident_agent.eval` scores **decision traces**, not just final labels:

1. Diagnosis accuracy  
2. Investigation efficiency  
3. Confidence calibration  
4. Remediation safety  
5. Recovery time (MTTR proxy)  

Dataset-layer eval (`datasets/eval.py`) remains available for prediction JSONL vs ground truth. Prefer extending pure functions in `eval/` when improving autonomy metrics.

---

## Extending the system (checklist)

1. Identify the layer  
2. Keep Stage-0 paths testable without network  
3. Add fakes for any new provider  
4. Update tests + [STATUS.md](STATUS.md) / README if user-visible behavior changes  
5. Delete dead code you replace  

Questions for maintainers: open a GitHub Issue with label-style title (`safety:`, `eval:`, `scenario:`).
