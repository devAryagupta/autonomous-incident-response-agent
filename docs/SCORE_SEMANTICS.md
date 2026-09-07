# Score semantics

Several values in this agent sit in `[0, 1]` and look interchangeable. They are not.
This page is the source of truth for what each number means.

Live graph path: `hypothesize` → `verify_hypotheses` → `plan_fix` → `validate_fix` → `confidence`.

| Number | Stored as | What it is | What it is not |
|--------|-----------|------------|----------------|
| Hypothesis belief | `Hypothesis.likelihood` | Current belief that this cause is true. Normalized prior after `hypothesize`; posterior after `verify_hypotheses`. | `P(evidence \| cause)`, and not `P(action succeeds)` |
| Baseline effectiveness | `RemediationTemplate.base_confidence` | Catalog weight for how well this action fits the cause | A measured or Bayesian probability |
| Option suitability | `RemediationOption.confidence` | `base_effectiveness × hypothesis belief` | Probability the action will fix the incident |
| Safety rank | `RemediationOption.safety_score` | Weighted mix of blast radius, reversibility, rollback, risk, and suitability | A probability |
| Diagnosis strength | `Diagnosis.confidence` | Heuristic certainty of the *category/symptom* label | A posterior over root causes |
| Routing / gate score | `ConfidenceScore.score` / `IncidentState.confidence_score` | Heuristic product used to replan vs enter execution | A calibrated `P(resolved)` |
| Calibrated confidence | `CalibratedAssessment.calibrated_confidence` | Library blend of predicted score, evidence quality, and history | The live graph routing score today |

Python aliases (same stored value, clearer name):

- `Hypothesis.belief` → `Hypothesis.likelihood`
- `RemediationOption.suitability` → `RemediationOption.confidence`
- `RemediationTemplate.base_effectiveness` → `RemediationTemplate.base_confidence`

---

## Diagnosis is scope, not a live cause

`diagnose` runs **once** after enrich. The category (OOMKilled vs Invalid
Configuration vs Application Failure) is the **incident scope**. Replanning
does not call `diagnose` again; it re-ranks hypotheses inside that catalog.

`Diagnosis.confidence` is certainty of that label, not a posterior over
causes and not a reason to rebuild the graph.

If later evidence drops the frozen family's signals and supports a different
family, `Diagnosis.scope_valid` becomes false and routing escalates. Mixed
evidence (original family still present) stays in the original scope.

---

## Hypothesis belief

`hypothesize` scores catalog priors against observations, then **normalizes** the
top candidates so they sum to 1. Those values are **priors** (belief before the
verification pass).

`verify_hypotheses` (live stack: `verification/engine.py`) treats that prior as
`P(H)`, updates it with a **discrete Bayes factor** from regex hits, then
**renormalizes** across the competing set. After that node,
`Hypothesis.likelihood` is **posterior belief**.

The field name `likelihood` is historical. In this codebase it means *current
belief*, not the statistical likelihood `P(E|H)`.

`HypothesisVerification.confidence_delta` is `posterior − prior` for that
hypothesis **before** cross-hypothesis renormalization. It is a belief change,
not a second probability.

`HypothesisVerification.result` is the **root-cause** verdict. A certain
diagnosis (OOMKilled, exit 137, CrashLoop) is **not** confirmation of leak vs
limit vs traffic. Required evidence on the spec must be present to confirm a
cause. For **Memory leak**, a single climb (`Memory increased from XMi to YMi`)
or a repeated CrashLoop sawtooth with similar peaks is not enough. Confirmation
needs rising cycle peaks or sustained growth that approaches the configured
limit. **Memory limit too low** still requires peak RSS ≥ 90% of the limit
(from `max_over_time` when Prometheus is attached). When every cause is
`inconclusive`, `chosen_hypothesis_id` stays empty and planning emits
investigate / NOOP. Renormalized belief may still rank the set; it is not
treated as a chosen root cause.

A second implementation (`verification/loop.py` + `HypothesisState`) runs
textbook `P(H|E) ∝ P(E|H) P(H)` on one OOM metric walkthrough. It is **not**
on the graph and does **not** write `IncidentState`. Roles and limits:
[VERIFICATION_STACKS.md](VERIFICATION_STACKS.md).

---

## Remediation suitability

Planning does **not** estimate `P(success)`.

```text
option.suitability = catalog.base_effectiveness × hypothesis.belief
```

`base_effectiveness` (`base_confidence` in the catalog) is how appropriate the
action is for that cause (restart is a poor leak fix; raising a memory limit is
a better fit). Multiplying by current belief prefers actions attached to the
better-supported cause. The product is still a **heuristic suitability score**.

The Bayesian update has already moved `hypothesis.belief`. The verification
verdict is not multiplied again here, and it is not multiplied again on the
routing / gate score.

`safety_score` then ranks *among* suitable options. It is also a heuristic.

Catalog ``purpose`` is not a score. For a given cause, the same action is one of:

| Purpose | Meaning |
|---------|---------|
| `root_cause` | Potential root-cause remediation |
| `mitigation` | Symptom control; **not** a root-cause fix |
| `temporary_recovery` | Service may come back; the cause is unchanged |
| `investigate` | No automated change |

Example: `increase_memory_limit` is `root_cause` for **Memory limit too low**
and `mitigation` for **Memory leak**. `restart_pod` is always
`temporary_recovery`. Memory may treat only `root_cause` as
`root_cause_verified`.

Do not feed `RemediationOption.confidence` into Brier scores, ECE, or
`P(resolved)` calibration as if it were a probability of success.

---

## Routing / gate score

`compute_confidence` (graph node `confidence`) builds a **routing score**:

```text
gate_score ≈ top_belief × validation_pass
```

`top_belief` is already the posterior. The same `HypothesisVerification.result`
that chose the Bayes factor is **not** applied again as a `verification_factor`.
Validation is a different signal (plan structure), so it stays.

That score decides replan vs enter the execution lifecycle. It is not claimed to
be a well-calibrated probability. `ConfidenceCalibrator` is a separate library
used when an explicit `CalibratedAssessment` is supplied; the default graph path
does not run it as its own stage.

---

## Outcome layers (not one SUCCESS)

Post-execution `OutcomeVerification.resolved` / `IncidentState.incident_resolved`
means **the service recovered** (expected health outcomes were observed). That is
not evidence the root cause was fixed.

`ResolutionAssessment` records four layers:

| Flag | Question | Stage-0 source |
|------|----------|----------------|
| `execution_success` | Did the command succeed? | `ExecutionResult.success` |
| `service_recovered` | Did the service become healthy? | expected outcomes met |
| `stable_recovery` | Did it remain healthy? | non-palliative action plus an explicit stability check (`restart count decreases`, `crashloop clears`, …). False when we only saw a snapshot after a restart. |
| `root_cause_verified` | Was the cause addressed? | recovered service **and** the action is a known causal pair for the confirmed hypothesis |

Restarting a leaking pod that comes back Ready:

```text
execution_success = true
service_recovered = true
stable_recovery = false
root_cause_verified = false
```

Memory episode `SUCCESS` is derived only from `root_cause_verified`. Temporary
recovery is `PARTIAL`. Prior boosts ignore episodes that are not
`root_cause_verified`, so history cannot claim “restart fixes memory leak.”
