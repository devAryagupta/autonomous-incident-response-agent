# CrashLoopBackOff synthetic dataset

First incident class for reproducible evaluation. Each line of the JSONL file is one `CrashLoopBackOffIncident` ([schema.py](schema.py)).

This dataset backs:

- Root-cause identification (`category` + `root_cause`)  
- Fix recommendation checks (`expected_fix`)  
- Agent lifecycle / benchmark plumbing  

Contributing new scenarios? See **[CONTRIBUTING.md §1](../../../../CONTRIBUTING.md#1-add-incident-scenarios)**.

---

## Generate

```bash
python -m incident_agent.datasets.crashloopbackoff.generate \
  --out "./data/synthetic/crashloopbackoff/incidents.jsonl" \
  --n 50
```

Windows PowerShell:

```powershell
python -m incident_agent.datasets.crashloopbackoff.generate `
  --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" `
  --n 50
```

---

## Incident shape (fields you will use)

| Field | Role |
|-------|------|
| `alert` | Normalized CrashLoopBackOff alert |
| `logs` / `events` | What diagnosis & evidence consume |
| `category` | Discrete failure class (eval label) |
| `root_cause` | Ground-truth cause string |
| `expected_fix` | Target remediation summary / hints |
| `target` | K8s namespace / kind / name |

---

## Categories (current)

**Image:** `invalid_image_wrong_tag`, `invalid_image_deleted_image`, `invalid_image_private_registry_auth`  

**Application:** `missing_secret`, `missing_env_var`, `bad_config`, `startup_exception`  

**Resource:** `oom`, `disk_pressure`, `cpu_starvation`  

**Dependency:** `dependency_database_unavailable`, `dependency_redis_unavailable`, `dependency_dns_failure`  

Legacy aliases remain for older fixtures (e.g. `resource_constraint_oom`, `app_bug_unhandled_exception`).

---

## How to add a scenario

1. Add or extend fixtures in `fixtures/crashloop_fixtures.json` (consumed by `generate.py`)  
2. Add the category to `CrashLoopCategory` in `schema.py` if it is new  
3. Ensure every incident has realistic `logs` / `events` and ground-truth `root_cause` + `expected_fix`  
4. Generate a small sample and run tests:

```bash
pytest tests -q -k crashloop
```

5. Document the failure mode in this README (one short paragraph is enough)

**Do not commit** large generated JSONL under `data/synthetic/` unless maintainers ask for a tiny checked-in sample.

---

## Evaluate predictions (dataset layer)

Predictions file (`.jsonl`), one object per incident id:

```json
{
  "schema_version": "1",
  "incident_id": "clb-...",
  "predicted_category": "missing_env_var",
  "predicted_root_cause": "...",
  "predicted_fix_summary": "..."
}
```

```bash
python -m incident_agent.datasets.eval \
  --dataset "./data/synthetic/crashloopbackoff/incidents.jsonl" \
  --predictions "./predictions.jsonl"
```

For autonomy scorecards over full decision traces, see `incident_agent.eval` and [docs/ARCHITECTURE.md](../../../../docs/ARCHITECTURE.md).
