# LLM Boundary (Suggest, Not Control)

This document defines how LLM capability enters the system without breaking the
deterministic safety model.

## Non-negotiable rule

The LLM may **suggest** hypotheses, evidence probes, or remediation ideas.
The LLM may **not** directly control routing, approval, execution, or policy.

Deterministic code remains the decision authority for:

- `route_on_confidence` (`execute` vs `replan` vs `escalate`)
- approval gating and execution policy
- action allowlisting and preconditions
- final mutation decisions

## Contract boundary

We define an explicit boundary before any model integration:

- `LLMSuggestionRequest`: read-only reasoning context for one stage
- `LLMSuggestion`: one advisory suggestion
- `LLMSuggestionResponse`: provider output for one stage
- `LLMSuggestionProvider`: interface for future model-backed providers
- `ingest_suggestions(...)`: deterministic validation/rejection gate
- `NoopLLMSuggestionProvider`: deterministic default (returns no suggestions)

These are implemented in:

- `src/incident_agent/contracts/llm.py`
- `src/incident_agent/llm/provider.py`
- `src/incident_agent/llm/ingestion.py`
- `src/incident_agent/llm/openai_provider.py`

## Guardrails encoded in contracts

`LLMSuggestion.metadata` rejects control-plane keys such as:

- `route`, `decision`, `approval`, `execute`
- `confidence_threshold`, `max_replans`, `replan_count`
- `execution_plan`, `fix_plan`, `phase`

`LLMSuggestionResponse` also enforces stage scoping: all suggestions inside one
response must target the same stage.

## Current integration scope

Advisory-only integration points now wired:

1. `hypothesize`: candidate causes to review
2. `collect_evidence`: extra evidence requests to consider

Any accepted suggestion must still pass deterministic validation, scoring, and
safety policy checks.

Out of scope in this milestone:

- remediation suggestions
- execution decisions
- route/approval/control-plane mutations

## Security flow

```text
LLM response
   -> ingest_suggestions (schema + stage + target + control-plane checks)
   -> advisory candidates only (hypothesis/evidence)
   -> existing deterministic verifier/planner
   -> existing confidence router + execution policy
```

A malicious suggestion like `execute rollout_restart immediately` is rejected by
ingestion and never reaches decision/route/execution fields.

## OpenAI / OpenRouter provider scope

`OpenAILLMSuggestionProvider` is intentionally narrow:

- accepts `LLMSuggestionRequest`
- calls one model for stage `hypothesize` or `collect_evidence`
- returns `LLMSuggestionResponse`
- on key/API/parse failure returns empty suggestions with warnings (fail-closed)

`OpenAILLMSuggestionProvider.from_env()` prefers OpenRouter when
`OPENROUTER_API_KEY` is set, and defaults to `qwen/qwen-2.5-7b-instruct`.
OpenRouter uses `json_object` (not strict JSON schema) because small instruct
models often reject `json_schema`.

Stage-0 `default_providers()` still uses `NoopLLMSuggestionProvider` so CI
never needs a key. Pass `llm=OpenAILLMSuggestionProvider.from_env()` to enable.

It does not implement remediation, execution, routing, or confidence decisions.

## Baseline-first workflow

Before adding any LLM implementation, freeze deterministic baselines:

```bash
python -m incident_agent.eval.freeze_baseline --label YYYY-MM-DD
```

Then compare future LLM-assisted runs against that frozen baseline using the
same dataset and score semantics:

```bash
python -m incident_agent.eval.compare_llm --baseline docs/baselines/2026-09-07.json --label YYYY-MM-DD-qwen
```

The comparison records diagnosis / top-hypothesis / fix accuracy plus LLM
activity: suggestions generated, accepted, rejected, useful evidence
requests, and unsafe/control suggestions. Raw suggestion text and rejection
reasons are stored on the reasoning trace.

Diagnosis accuracy is not expected to rise. The model is not the diagnoser.
A flat or lower fix score with zero unsafe accepted is a valid result: it
shows whether advisory suggestions changed the downstream commit, and why
the deterministic gate still owns execute vs escalate.

## Measured Stage-0 close

Same 9 golden incidents. Catalog IDs were verified at run time; they are not
a promise that a given slug stays free or even listed.

| Metric | Deterministic | Qwen 2.5 7B | Gemma 3 27B |
|---|---|---|---|
| Diagnosis accuracy | 9/9 | 9/9 | 9/9 |
| Top hypothesis | 7/9 | 7/9 | 7/9 |
| Fix accuracy | 5/9 | 2/9 | 2/9 |
| Insufficient evidence | 4/9 | 7/9 | 7/9 |
| LLM acceptance | N/A | 81/128 (63%) | 89/138 (64%) |
| Useful evidence | N/A | 12 | 20 |
| Unsafe accepted | N/A | 0 | 0 |

Write-ups: [baselines/2026-09-08-qwen.md](baselines/2026-09-08-qwen.md),
[baselines/2026-09-08-gemma.md](baselines/2026-09-08-gemma.md).

Both models added candidates. Neither owned diagnosis. Both dropped three
near-threshold executes below the 0.7 gate after advisory hypotheses were
merged and renormalized. The planner still proposed the same remediations;
the gate refused to commit. That is the wrap for this milestone.
