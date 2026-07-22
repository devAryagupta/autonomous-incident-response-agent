# CrashLoopBackOff synthetic dataset

First incident class for evaluating the agent. Each line of the JSONL is one
`CrashLoopBackOffIncident` (`schema.py`, `schema_version="1"`).

## What it evaluates

- Root-cause identification (`category` + `root_cause`)
- Hypothesis ranking (via eval metrics against canonical cause names)
- Fix recommendation (`expected_fix.summary` / `kind` / `kubectl_hint`)
- Downstream agent plumbing (validation / confidence when predictions include those fields)

## Layout

| Path | Role |
|------|------|
| `schema.py` | `CrashLoopBackOffIncident`, categories, `ExpectedFix` |
| `fixtures/crashloop_fixtures.json` | Log/event templates per category |
| `generate.py` | Seeded CLI generator |
| `../../core.py` | Shared JSONL load/write |
| `../../eval.py` | Evaluation CLI + metrics |

## Generate

```powershell
python -m incident_agent.datasets.crashloopbackoff.generate `
  --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" `
  --n 50 `
  --seed 1337
```

- `--n` defaults to `25` if omitted  
- `--seed` defaults to `1337` (reproducible IDs and category sampling)  
- Output path is typically gitignored under `data/synthetic/`

## Categories (fixtures)

| Group | Categories |
|-------|------------|
| Invalid image | `invalid_image_wrong_tag`, `invalid_image_deleted_image`, `invalid_image_private_registry_auth` |
| Application | `missing_secret`, `missing_env_var`, `bad_config`, `startup_exception` |
| Resource | `oom`, `disk_pressure`, `cpu_starvation` |
| Dependency | `dependency_database_unavailable`, `dependency_redis_unavailable`, `dependency_dns_failure` |

`schema.py` also keeps **legacy** category names for older datasets/tests
(e.g. `resource_constraint_oom`, `app_bug_unhandled_exception`).

## Incident shape (fields the agent uses)

| Field | Role |
|-------|------|
| `incident_id`, `created_at` | Identity |
| `target` | `K8sRef` (namespace, kind, name) |
| `alert` | Normalized CrashLoopBackOff alert |
| `logs` / `events` | Signals for enrich → diagnose / hypothesize |
| `category`, `root_cause` | Ground truth for eval |
| `expected_fix` | Ground-truth fix summary / kind / kubectl hint |
| `distractors` | Reserved for harder eval (generator currently sets `[]`) |

## How this connects to the agent

`run_pipeline.py` maps each dataset incident into `IncidentState` (alert + observations),
then runs `GRAPH.invoke` (enrich → … → finalize). Dataset ground truth is **not**
copied into agent state except via what the nodes infer from logs/events.

See the root [README.md](../../../../README.md) for the full lifecycle and eval/benchmark commands.
