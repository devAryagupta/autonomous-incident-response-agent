# Two Bayesian stacks

This repo has **two** belief-update implementations. They are not two
layers of one system. They do not share types, they do not share evidence
schemas, and only one of them runs on the live incident graph.

| | Live graph stack | Standalone OOM loop |
|--|------------------|---------------------|
| Job | Update cause **belief** on `IncidentState` after `collect_evidence` | Walk through one OOM metric example: series → `P(E\|H)` matrix → posterior |
| Entry | `nodes/verify_hypotheses.py` → `verification/engine.py` | `verification/loop.py` → `verification/bayesian.py` |
| Types | `contracts.Hypothesis`, `HypothesisVerification` | `hypothesis.models.HypothesisState` |
| Evidence | Lines already on `Observations` (logs / events / merged metrics) | `evidence.models.TelemetryResult` numeric series |
| Catalog | Regex specs in `verification/specs.py` (OOM, config, app) | One hand-set matrix: Continuous Memory Growth |
| Who calls it | LangGraph, `pipeline.py`, golden eval | Library + `tests/test_bayesian_verification.py` |
| Writes | `Hypothesis.likelihood` (prior in, posterior out) | `HypothesisState.posterior_probability` |
| Used to route / plan / execute? | Yes — posterior belief feeds plan and the gate score | No |

Do **not** merge them unless a real bug requires one payload. Convert at a
boundary if a future stage wants the loop’s metric math on the graph.

See [SCORE_SEMANTICS.md](SCORE_SEMANTICS.md) for what the numbers mean.

---

## 1. Live graph stack — belief update

**Role:** After evidence is collected onto `IncidentState`, challenge each
competing cause and rewrite `Hypothesis.likelihood` as **posterior belief**.

```text
prior belief  →  regex challenge  →  Bayes factor  →  odds update  →  renormalize
```

`verify_hypotheses` treats `Hypothesis.likelihood` as `P(H)`, multiplies
prior odds by a discrete Bayes factor (`confirmed` / `inconclusive` /
`contradicted`), then renormalizes so the set sums to 1. The field name
`likelihood` is historical: it is **current belief**, not `P(E|H)`.

This is the only stack on `START → … → verify_hypotheses → plan_fix`.

### What it is not

- Not a generative model. Bayes factors are **fixed buckets**
  (`6.0` / `2.5` / `0.85` / `0.65` / `0.20`), not estimated `P(E|H)`.
- Not `P(action succeeds)` and not a calibrated probability.
- Not a sequential filter over independent observations. Each pass
  re-scores the **current** observation blob. Replan repeats that on
  accumulated lines; it does not multiply independent evidence terms.
- Not “none of the above” for **belief numbers**. Renormalization still
  forces the open set to sum to 1. Planning does **not** treat that
  ranking as a chosen cause when every `HypothesisVerification.result`
  is `inconclusive`.
- Not a live metrics pull. It only reads what `collect_evidence` already
  merged into `Observations`.
- Independent-evidence is assumed and is false: one log line can hit
  several specs.
- Does not use `HypothesisState`, `bayesian_update`, or
  `evidence.models.TelemetryResult`.

---

## 2. Standalone OOM loop — metric walkthrough

**Role:** A **library** that shows textbook discrete Bayes on one OOM
discrimination story (leak vs low limit vs traffic), driven by a memory
time series.

```text
P(H_i | E) ∝ P(E | H_i) × P(H_i)
```

`run_bayesian_verification_loop` looks at Prometheus-shaped datapoints.
If memory is judged monotonically increasing, it applies the
**Continuous Memory Growth** matrix (`0.95` / `0.35` / `0.05` on the
three fixed OOM hypotheses). Priors and posteriors live on separate
fields (`prior_probability`, `posterior_probability`).

This loop is **not** a graph node and does **not** write `IncidentState`.

### What it is not

- Not the live verifier. The graph never calls it.
- Not a general cause catalog. Exactly three OOM hypotheses; no config
  or app-failure causes.
- `P(E|H)` values are **invented for a walkthrough**, not fit from data.
  A second matrix (`LIKELIHOOD_CONTINUOUS_MEMORY_GROWTH_SPEC`) exists
  only so a cited example can hit a target posterior.
- Only **one** evidence class updates posteriors. Traffic-spike detection
  is recorded as a boolean and is **not** applied as a likelihood update.
- Status cutoffs (`CONFIRMED ≥ 0.70`, `PARTIAL ≥ 0.20`) are arbitrary
  labels on that posterior, not the live `HypothesisVerification.result`.
- Uses a **second** evidence schema (`evidence.models.EvidenceRequest` /
  `TelemetryResult`), not `contracts.EvidenceRequest`.
- The monotonic-increase detector is a heuristic (≥80% of steps up, end
  above start). It is not proof of a leak.

---

## How to talk about this

Say: *the graph updates belief with Bayes-factor buckets on observation
text; the OOM loop is a separate, uncalibrated metric demo.*

Do not say: *we run Bayesian inference*, or *the two stacks are prior and
posterior of the same model.*
