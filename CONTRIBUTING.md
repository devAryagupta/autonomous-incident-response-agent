# Contributing

Thanks for contributing to the **autonomous incident-response agent**.

By submitting a change, you agree to follow the same structure and coding format as the rest of the repo.

## Required reading

1. **[docs/CODING_PRINCIPLES.md](docs/CODING_PRINCIPLES.md)** — architecture layers, naming, size limits, SoC, runtime vs source  
2. **[README.md](README.md)** — Stage-0 scope, quickstart, benchmarks  

If a PR violates those principles (wrong layer, `utils/` dump, runtime files under `src/`, cryptic names), it will be asked to change before merge.

## Development setup

```powershell
cd "d:\autonomous incident-response agent"
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev,agent]"
```

## Checks before you open a PR

```powershell
ruff check src tests
ruff format --check src tests
pytest
```

Fix failures locally. Do not bypass hooks or commit secrets (`.env`, credentials).

## Where to put new code

| If you are adding… | Put it in… |
|--------------------|------------|
| Pydantic / state types | `src/incident_agent/contracts/` |
| Diagnose / hypothesize / plan / validate / score logic | `nodes/` (target: `business/`) — **not** in `graph.py` |
| Graph edges, replan, pipeline order | `graph.py` / `pipeline.py` / `routing.py` (target: `orchestration/`) |
| Logs/events/metrics/execution/memory backends | `src/incident_agent/providers/` |
| Prompt templates | `prompts/` when created — **never** inline in business nodes long-term |
| LLM client wrappers | `llm/` when created |
| Kubectl / HTTP / cluster tools | `tools/` when created |
| Synthetic dataset schemas / generators | `src/incident_agent/datasets/` |
| Run logs, memory dumps, checkpoints | `runtime/` (gitignored) — **never** under `src/` |

Full rules: [docs/CODING_PRINCIPLES.md](docs/CODING_PRINCIPLES.md).

## Style checklist (short)

- [ ] Descriptive names (`snake_case` modules/functions, `PascalCase` classes, `UPPER_SNAKE_CASE` constants)
- [ ] Booleans prefixed: `is_`, `has_`, `can_`, `should_`
- [ ] Line length ≤ 100; keep functions small (prefer ≤ ~40 lines)
- [ ] Single responsibility; extract named sub-functions instead of growing one blob
- [ ] No new `utils/` / `helpers/` packages
- [ ] No dead code left behind
- [ ] Tests updated for behavior changes
- [ ] Package nesting stays within 3–4 levels under `incident_agent`

## Commit messages

Prefer short, imperative summaries focused on **why**:

- `Add confidence replan loop to LangGraph orchestration`
- `Isolate observation fetching behind ObservationProvider`

Avoid noisy commits that mix unrelated refactors with feature work. Split PRs when a change spans many layers.

## Pull requests

Include:

1. What changed and which **layer** it belongs to  
2. How you tested (`pytest`, manual CLI if relevant)  
3. Any follow-ups (e.g. “stub diagnose still hardcoded”)  

Do not commit generated JSONL under `data/synthetic/`, benchmark `artifacts/`, or anything under `runtime/` except intentional placeholder docs.
