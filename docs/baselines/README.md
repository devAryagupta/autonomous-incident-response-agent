# Baseline Snapshots

This folder stores deterministic baseline manifests that are committed to git.
Generated runtime artifacts stay under `artifacts/baselines/` (gitignored).

## Freeze command

```bash
python -m incident_agent.eval.freeze_baseline --label YYYY-MM-DD
```

Outputs:

- `docs/baselines/YYYY-MM-DD.json` (machine-readable snapshot)
- `docs/baselines/YYYY-MM-DD.md` (human-readable summary)
- `artifacts/baselines/YYYY-MM-DD/*` (detailed run artifacts)

## Why this exists

Before introducing model-assisted reasoning, we lock deterministic metrics so
future LLM-assisted changes answer one question clearly:

**Did this improve the agent over the deterministic baseline?**

## LLM comparison

After a freeze exists, run the same golden set with an env-configured LLM:

```bash
python -m incident_agent.eval.compare_llm --baseline docs/baselines/2026-09-07.json --label YYYY-MM-DD-qwen
```

That writes `docs/baselines/<label>.md` plus gitignored traces under
`artifacts/baselines/<label>/`. Traces keep raw suggestions and rejection
reasons. Stage-0 CI still uses the no-op LLM.

## Current snapshots

| Snapshot | Role |
|---|---|
| [2026-09-07.md](2026-09-07.md) | Frozen deterministic Stage-0 (diagnosis 9/9, top hyp 7/9, fix 5/9) |
| [2026-09-08-qwen.md](2026-09-08-qwen.md) | Same 9 cases, OpenRouter Qwen 2.5 7B instruct |
| [2026-09-08-gemma.md](2026-09-08-gemma.md) | Same 9 cases, OpenRouter Gemma 3 27B instruct |

| Metric | Deterministic | Qwen | Gemma |
|---|---|---|---|
| Diagnosis | 9/9 | 9/9 | 9/9 |
| Top hypothesis | 7/9 | 7/9 | 7/9 |
| Fix | 5/9 | 2/9 | 2/9 |
| Insufficient evidence | 4/9 | 7/9 | 7/9 |
| LLM acceptance | N/A | 63% | 64% |
| Unsafe accepted | N/A | 0 | 0 |

The catalog ID used at run time is recorded in each LLM manifest. Do not
treat slugs or “free” status as stable.

```bash
python -m incident_agent.eval.compare_llm --model google/gemma-3-27b-it --label YYYY-MM-DD-gemma
```
