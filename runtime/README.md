# Runtime directory

Agent **runtime data** lives here — not under `src/`.

| Subfolder | Purpose |
|-----------|---------|
| `logs/` | Run / debug logs |
| `memory/` | Memory provider dumps / retrieval caches |
| `state/` | Checkpoints / persisted incident state |
| `artifacts/` | Ad-hoc outputs from local runs |

These paths are gitignored. Do not commit secrets or large dumps.

Source code, prompts, and schemas belong under `src/incident_agent/` only.
See [docs/CODING_PRINCIPLES.md](../docs/CODING_PRINCIPLES.md) §5.
