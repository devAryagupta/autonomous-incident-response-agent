# Project status

**Stage:** 0 close — foundation frozen, advisory LLM measured  
**Package:** `incident-response-agent` `0.1.0`  
**Python:** ≥ 3.12  

This page is the transparent “what works today” checklist for collaborators. When in doubt, trust `src/` and update this file in the same PR.

---

## Snapshot

We are **not** claiming production on-call autonomy.

We **are** claiming a Stage-0 loop that is deterministic by default, eval-native, and safe enough to let a model *suggest* without letting it *decide*:

- A typed lifecycle (`IncidentState`) from enrich → finalize  
- A frozen 9-case CrashLoopBackOff golden set  
- Provider-swappable I/O (synthetic by default; K8s / Prometheus / kubectl opt-in)  
- Explicit remediation safety gates before mutation  
- Advisory LLM behind `LLMSuggestionProvider` + ingestion  
- Measured compares: diagnosis stayed 9/9; fix 5/9 → 2/9; unsafe accepted 0  

The LLM result is a wrap of this milestone, not a failure. The model is not the diagnoser. Extra advisory hypotheses diluted near-threshold belief and the gate refused to commit. That is why the deterministic boundary exists.

---

## Architecture cleanup (7 tasks)

The graph shape stays. Each task fixes one interviewer-facing inconsistency.
No new packages or stages unless a later task proves the current stage cannot
own the job. Score meanings: [SCORE_SEMANTICS.md](SCORE_SEMANTICS.md).

| # | Task | One job | Status |
|---|------|---------|--------|
| 1 | **Score terminology** | Hypothesis values = belief after evidence; remediation scores = heuristic suitability, not `P(success)` | Done (this pass) |
| 2 | **One live confidence function** | Retire unused `score_confidence`; keep `compute_confidence` as the routing-score node | Open |
| 3 | **Don't double-count verification in the gate score** | After verify, `Hypothesis.likelihood` is already posterior; the gate formula must not pretend it is a second independent probability | Done (this pass) |
| 4 | **Name the two verification stacks** | Live path = `verification/engine.py` + `contracts.Hypothesis`; standalone OOM loop = `verification/loop.py` + `HypothesisState`. Document, don't merge unless a real bug requires it | Done — [VERIFICATION_STACKS.md](VERIFICATION_STACKS.md) |
| 5 | **One job per reasoning stage** | Diagnose = one-shot scope (frozen on replan); hypothesize = prior belief inside that scope; verify = update belief (+ invalidate scope if evidence contradicts it); plan = choose action; `validate_fix` = plan structure; `pre_execute_validate` = execution preconditions | Partial (diagnosis freeze) |
| 6 | **Honest calibration boundary** | `ConfidenceCalibrator` is a library at the execution gate when an assessment is supplied — not a hidden extra graph stage and not the same number as hypothesis belief | Open |
| 7 | **One public gate-score field** | Collapse `IncidentState.confidence` vs `confidence_score` to one source of truth (keep a compatibility alias if needed) | Open |

---

## Capability matrix

| Capability | State | Notes |
|------------|-------|-------|
| CrashLoopBackOff dataset + generator | Shipped | Categories for image / app / resource / dependency (+ legacy aliases) |
| Dataset prediction eval CLI | Shipped | `python -m incident_agent.datasets.eval` |
| Benchmark baselines (oracle / empty / heuristic) | Shipped | Plumbing validation |
| LangGraph orchestration + replan | Shipped | Diagnose once (scope); replan → hypothesize only; scope contradiction or exhausted low confidence → NOOP |
| Deterministic pipeline parity | Shipped | Prefer both in tests |
| Diagnosis / hypothesis engines | Shipped | Deterministic Stage 0 |
| Evidence planner + collection node | Shipped | |
| Hypothesis verification (Bayesian / specs) | Shipped | Two stacks: live Bayes-factor update vs standalone OOM loop — see [VERIFICATION_STACKS.md](VERIFICATION_STACKS.md) |
| Remediation catalog + plan validation | Shipped | |
| Confidence engine + calibration | Shipped | Gates mutation when below threshold |
| Local episode memory | Shipped | Under `runtime/` when persisted; learns only from `root_cause_verified`, not service recovery |
| Execution lifecycle nodes | Shipped | prepare → pre-validate → approve → execute → verify outcome. Pre-validate is a small state gate; kubectl execution GETs the target |
| Dry-run execution provider | Shipped | Default-safe |
| Kubectl allowlisted execution | Shipped (opt-in) | Fake client for tests; live via extra `k8s` |
| Kubernetes observation provider | Shipped (opt-in) | Maps cluster → `Observations` |
| Prometheus metrics provider | Shipped (opt-in) | PromQL → verification loop |
| Multi-dimensional scorecard (`eval/`) | Shipped | Five dimensions; failed golden rows get a stage label, not extra metrics |
| Baseline freeze CLI + manifests | Shipped | `python -m incident_agent.eval.freeze_baseline --label <date>` |
| LLM-assisted golden comparison | Shipped | `python -m incident_agent.eval.compare_llm --model <id>` — [Qwen](baselines/2026-09-08-qwen.md) and [Gemma](baselines/2026-09-08-gemma.md) |
| LLM suggestion ingestion boundary | Shipped | Wired at `hypothesize` + `collect_evidence`; advisory-only, no control-plane mutations |
| OpenAI-compatible LLM provider | Shipped | `from_env()` prefers OpenRouter; model is hidden behind `LLMSuggestionProvider` |
| Dedicated `prompts/` package | Open | Provider still owns the small stage prompts; extract later if templates grow |
| More incident classes | Open | Community scenarios welcome |
| Human approval UX / ticketing | Planned | Policy already can require human approval |
| Production deployment / HA agent | Not started | Out of Stage 0 scope |

---

## What is intentionally stubbed or limited

- **Default backends are synthetic / dry-run** so CI never needs a cluster  
- **Pre-execute is a short checklist** — not a policy engine. DryRun infers target presence; kubectl execution GETs the resource (SDK equivalent of `kubectl get`)
- **LLM is opt-in and advisory-only** — CI uses `NoopLLMSuggestionProvider`. Qwen and Gemma both left diagnosis at 9/9 and dropped fix from 5/9 to 2/9 with zero unsafe accepted. See [baselines/README.md](baselines/README.md).  
- **Allowlist is small on purpose** — expanding actions is a safety review, not a race  
- **Memory is local-first** — not a distributed knowledge base yet  
- **Stable recovery is snapshot-only** — Stage 0 has no dwell-time window; a healthy-after-restart episode is never `root_cause_verified`  
- **Diagnosis is frozen on replan** — it is coarse scope, not a live category tracker. Strong contradiction escalates instead of re-diagnosing  
- **Two Bayesian stacks stay separate** — the graph uses Bayes-factor buckets on text; the OOM loop is an uncalibrated metric demo, not a hidden extra stage  
- **License file** may still be pending — add before wide public launch  

---

## Roadmap themes (community-shaped)

Ordered by how much we want outside help:

1. **Scenarios & simulations** — harder fixtures, new incident types, kind/k3d recipes  
2. **SRE workflow reviews** — stop conditions, blast radius, change-freeze awareness  
3. **Eval metrics** — over-mutation penalties, calibration on real traces  
4. **Safety policy** — thresholds, action risk ratings, auditability  
5. **LLM follow-ups** — dedicated `prompts/`, less belief-diluting merge, remediation suggestions still out of scope until the gate story is clearer  

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

---

## Stage-0 wrap

This is a good place to stop adding Stage-0 surface area.

Done for this close:

1. Deterministic golden freeze ([2026-09-07](baselines/2026-09-07.md))  
2. Suggest-not-control LLM boundary ([LLM_BOUNDARY.md](LLM_BOUNDARY.md))  
3. Same 9 incidents compared with two OpenRouter models  

What the numbers mean: diagnosis accuracy is not an LLM metric. Success was “did advisory suggestions improve the commit without taking control?” They did not improve the commit. They also did not bypass the gate. That is the result to carry forward.
