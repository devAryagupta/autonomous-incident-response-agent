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

## Hypothesis belief

`hypothesize` scores catalog priors against observations, then **normalizes** the
top candidates so they sum to 1. Those values are **priors** (belief before the
verification pass).

`verify_hypotheses` treats that prior as `P(H)`, updates it with a Bayes factor
from observed evidence, then **renormalizes** across the competing set. After
that node, `Hypothesis.likelihood` is **posterior belief**.

The field name `likelihood` is historical. In this codebase it means *current
belief*, not the statistical likelihood `P(E|H)`.

`HypothesisVerification.confidence_delta` is `posterior − prior` for that
hypothesis **before** cross-hypothesis renormalization. It is a belief change,
not a second probability.

The standalone OOM loop (`verification/loop.py`, `HypothesisState`) already uses
`prior_probability` / `posterior_probability`. That loop is not the LangGraph
payload; see cleanup task 4.

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
