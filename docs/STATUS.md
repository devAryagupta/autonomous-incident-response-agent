# Project status

**Stage:** 0 — foundation (contracts, synthetic eval, gated execution)  
**Package:** `incident-response-agent` `0.1.0`  
**Python:** ≥ 3.12  

This page is the transparent “what works today” checklist for collaborators. When in doubt, trust `src/` and update this file in the same PR.

---

## Snapshot

We are **not** claiming production on-call autonomy yet.

We **are** claiming:

- A typed lifecycle (`IncidentState`) from enrich → finalize  
- Reproducible CrashLoopBackOff incidents for benchmarks  
- Provider-swappable I/O (synthetic by default; K8s / Prometheus / kubectl opt-in)  
- Explicit remediation safety gates before mutation  
- Multi-dimensional evaluation over decision traces  

---

## Capability matrix

| Capability | State | Notes |
|------------|-------|-------|
| CrashLoopBackOff dataset + generator | Shipped | Categories for image / app / resource / dependency (+ legacy aliases) |
| Dataset prediction eval CLI | Shipped | `python -m incident_agent.datasets.eval` |
| Benchmark baselines (oracle / empty / heuristic) | Shipped | Plumbing validation |
| LangGraph orchestration + replan | Shipped | Conditional edges on confidence |
| Deterministic pipeline parity | Shipped | Prefer both in tests |
| Diagnosis / hypothesis engines | Shipped | Deterministic Stage 0 |
| Evidence planner + collection node | Shipped | |
| Hypothesis verification (Bayesian / specs) | Shipped | |
| Remediation catalog + plan validation | Shipped | |
| Confidence engine + calibration | Shipped | Gates mutation when below threshold |
| Local episode memory | Shipped | Under `runtime/` when persisted |
| Execution lifecycle nodes | Shipped | prepare → pre-validate → approve → execute → verify outcome |
| Dry-run execution provider | Shipped | Default-safe |
| Kubectl allowlisted execution | Shipped (opt-in) | Fake client for tests; live via extra `k8s` |
| Kubernetes observation provider | Shipped (opt-in) | Maps cluster → `Observations` |
| Prometheus metrics provider | Shipped (opt-in) | PromQL → verification loop |
| Multi-dimensional scorecard (`eval/`) | Shipped | Five dimensions; equal weights by default |
| LLM reasoning / prompt packages | Planned | Create `llm/` + `prompts/`; do not inline into nodes |
| More incident classes | Open | Community scenarios welcome |
| Human approval UX / ticketing | Planned | Policy already can require human approval |
| Production deployment / HA agent | Not started | Out of Stage 0 scope |

---

## What is intentionally stubbed or limited

- **Default backends are synthetic / dry-run** so CI never needs a cluster  
- **LLM nodes are not the Stage-0 path** — heuristics prove the loop first  
- **Allowlist is small on purpose** — expanding actions is a safety review, not a race  
- **Memory is local-first** — not a distributed knowledge base yet  
- **License file** may still be pending — add before wide public launch  

---

## Roadmap themes (community-shaped)

Ordered by how much we want outside help:

1. **Scenarios & simulations** — harder fixtures, new incident types, kind/k3d recipes  
2. **SRE workflow reviews** — stop conditions, blast radius, change-freeze awareness  
3. **Eval metrics** — over-mutation penalties, calibration on real traces  
4. **Safety policy** — thresholds, action risk ratings, auditability  
5. **LLM layer (later)** — only behind `prompts/` + `llm/`, measured against the same scorecard  

---

## How to verify locally

```bash
pip install -e ".[dev,agent]"
pytest
ruff check src tests
```

Optional live extras: `pip install -e ".[k8s]"`.

---

## Doc freshness

| Source of truth | Use for |
|-----------------|---------|
| This file + root README | Current status |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Structure & workflow |
| [progressreport1.md](../progressreport1.md) | **Historical** early Stage-0 snapshot only |

If you ship a user-visible capability, update the README status table and this matrix in the same PR.
