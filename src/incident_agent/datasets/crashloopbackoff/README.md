# CrashLoopBackOff synthetic dataset

Each line is one JSON document (`.jsonl`) matching `CrashLoopBackOffIncident` from `schema.py`.

This is the **first incident class** we’ll use to evaluate:

- Root-cause identification (category + `root_cause`)
- Fix recommendation (match `expected_fix.summary` / `kubectl_hint`)

## Generate

```powershell
python -m incident_agent.datasets.crashloopbackoff.generate --out ".\data\synthetic\crashloopbackoff\incidents.jsonl" --n 50
```

## Example incident (shape)

Fields you’ll most often use in the agent:

- `alert`: the normalized alert payload
- `logs` / `events`: what diagnosis consumes
- `root_cause` / `expected_fix`: ground truth for evaluation
