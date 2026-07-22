# Runtime directory

Agent **runtime data** lives here — not under `src/`.

Stage 0 does not write here yet by default (providers are in-memory / dry-run).
The layout is reserved so later memory dumps, checkpoints, and run logs have a
fixed home outside the package tree.

| Subfolder | Purpose |
|-----------|---------|
| `logs/` | Run / debug logs |
| `memory/` | Memory provider dumps / retrieval caches |
| `state/` | Checkpoints / persisted incident state |
| `artifacts/` | Ad-hoc outputs from local runs |

Related output dirs elsewhere (also gitignored):

- `data/synthetic/` — generated CrashLoop JSONL from the dataset CLI  
- `artifacts/` — benchmark `predictions.*.jsonl` / `report.*.json`  

Do not commit secrets or large dumps. Product source stays under `src/incident_agent/`.

See [docs/CODING_PRINCIPLES.md](../docs/CODING_PRINCIPLES.md) §5 and the root [README.md](../README.md).
