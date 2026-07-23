# Contributing

Thank you for helping build a **safety-first** autonomous incident-response agent.

This project is Stage 0: we optimize for **clear contracts, reproducible eval, and remediation safety** — not for shipping a reckless kubectl bot. The best contributions improve realism, metrics, or safety.

By submitting a change, you agree to follow the architecture and coding standards in this repo.

---

## Before you start

1. Read the root **[README.md](README.md)** (2-minute overview + status)  
2. Skim **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** and **[docs/CODING_PRINCIPLES.md](docs/CODING_PRINCIPLES.md)**  
3. Pick a contribution path below (or open an Issue to propose one)

If a PR violates layer boundaries (`utils/` dumps, kubectl inside business nodes, runtime files under `src/`), it will be asked to change before merge.

---

## Ways to contribute

These are the highest-leverage paths for the community right now.

### 1. Add incident scenarios

**Why it matters:** Eval quality is gated by scenario diversity. CrashLoopBackOff is the first class; we need more realistic cases and edge categories.

**What to do:**

- Extend fixtures / categories under `src/incident_agent/datasets/crashloopbackoff/`  
- Or propose a **new incident class** package (e.g. `ImagePullBackOff`, `OOMKilled`, pending Pods) mirroring the CrashLoop layout  
- Always include **ground truth**: `category`, `root_cause`, `expected_fix`  
- Keep telemetry realistic: logs + events an SRE would actually see  

**Good PR looks like:**

- Schema / fixture updates + generator still produces valid JSONL  
- At least one test asserting the new category loads and evaluates  
- Short note in the dataset README describing the failure mode  

**Start here:** [datasets/crashloopbackoff/README.md](src/incident_agent/datasets/crashloopbackoff/README.md)

```bash
python -m incident_agent.datasets.crashloopbackoff.generate \
  --out "./data/synthetic/crashloopbackoff/incidents.jsonl" \
  --n 20
pytest tests -q -k crashloop
```

---

### 2. Review SRE workflows

**Why it matters:** The graph encodes an opinionated on-call loop (enrich → diagnose → evidence → verify → plan → confidence → gated execute). We want practicing SREs to challenge it.

**What to do (docs + Issues count!):**

- Walk the workflow in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the diagram in the README  
- Open an Issue titled `SRE review: …` with:
  - What real teams do differently  
  - Missing signals (SLOs, runbooks, change freezes, blast radius)  
  - Unsafe assumptions in the current plan/approve path  
- Optional: PR that updates docs or adds a “runbook note” next to a node — no code required for a first contribution  

**Especially valuable feedback:**

- When should the agent **stop and page a human**?  
- What evidence is mandatory before restart vs scale vs limit changes?  
- How do you handle flapping / partial remediation?

---

### 3. Add Kubernetes failure simulations

**Why it matters:** Synthetic JSONL is great for CI; cluster-faithful simulations catch provider and evidence bugs.

**What to do:**

- Improve `KubernetesObservationProvider` mapping (pods / events / logs → `Observations`)  
- Add **fake client** fixtures that simulate failure modes (CrashLoop, OOM, probe failures, image pull errors)  
- Propose chaos / kind / k3d recipes in docs (keep secrets out of the repo)  
- Ensure business nodes stay source-agnostic — simulation belongs in `providers/` or `datasets/`, not in `diagnose`  

**Good PR looks like:**

- Deterministic fake-client test (no live cluster required in CI)  
- Clear failure mode name in the test / fixture  
- Optional: doc snippet under `docs/` for running against a local kind cluster  

```bash
pip install -e ".[dev,k8s]"
pytest tests -q -k kubernetes
```

---

### 4. Improve evaluation metrics

**Why it matters:** Autonomy without measurement is theater. We score traces on five dimensions today:

| Dimension | Intent |
|-----------|--------|
| Diagnosis accuracy | Confirmed hypothesis vs ground truth |
| Investigation efficiency | High-value evidence / total telemetry calls |
| Confidence calibration | Brier-style honesty of confidence |
| Remediation safety | Prefer lowest-risk adequate action |
| Recovery time (MTTR) | Steps + wall clock, normalized |

**What to do:**

- Tighten cause alias matching (`eval/metrics.py`)  
- Add metrics that punish **over-mutation** or **skipped validation**  
- Improve scorecard aggregation / weights with a written rationale  
- Add scenario-level regression tests when you change scoring  

**Good PR looks like:**

- Pure functions stay pure (no I/O in `eval/`)  
- Unit tests for new/changed metric edge cases  
- README or `docs/STATUS.md` note if the scorecard semantics change  

```bash
pytest tests -q -k eval
```

---

### 5. Review remediation safety policies

**Why it matters:** This is the line between helpful agent and production incident. Be skeptical.

**What to review / change:**

| Piece | Location |
|-------|----------|
| Risk × confidence thresholds | `src/incident_agent/execution/policy.py` |
| Allowlisted actions | `src/incident_agent/execution/actions/` |
| Preconditions / outcome checks | `execution/preconditions.py`, `execution/outcome.py` |
| Calibration gate | `src/incident_agent/calibration/` |
| Remediation catalog / options | `src/incident_agent/remediation/` |

**Ask hard questions:**

- Are CRITICAL actions ever auto-allowed too easily?  
- Should `scale_deployment` / `update_resource_limit` require stronger evidence?  
- Is dry-run the default in every public example?  
- Do we log enough for post-incident audit?

**Good first contribution:** Issue with a concrete policy proposal (threshold table + rationale). Follow-up PR with tests that prove the gate blocks unsafe paths.

```bash
pytest tests -q -k "execution or policy or calibrat"
```

---

## Development setup

```bash
git clone https://github.com/AryaNamekart/autonomous-incident-response-agent.git
cd autonomous-incident-response-agent

python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1

pip install -U pip
pip install -e ".[dev,agent]"
# Optional live cluster adapters:
# pip install -e ".[k8s]"
```

---

## Checks before you open a PR

```bash
ruff check src tests
ruff format --check src tests
pytest
```

Fix failures locally. Do **not** bypass hooks or commit secrets (`.env`, kubeconfigs with credentials, tokens).

---

## Where to put new code

| If you are adding… | Put it in… |
|--------------------|------------|
| Pydantic / state types | `src/incident_agent/contracts/` |
| Diagnose / hypothesize / plan / validate / score logic | Business packages (`nodes/`, `diagnosis/`, …) — **not** `graph.py` |
| Graph edges, replan, pipeline order | `graph.py` / `pipeline.py` / `routing.py` |
| Logs / events / metrics / execution / memory backends | `src/incident_agent/providers/` (or `execution/` for action handlers) |
| Prompt templates | `prompts/` when created — never inline in business nodes long-term |
| LLM client wrappers | `llm/` when created |
| Kubectl / HTTP / cluster helpers | `tools/` when created, or keep behind providers |
| Synthetic schemas / generators | `src/incident_agent/datasets/` |
| Scorecard / trace metrics | `src/incident_agent/eval/` |
| Run logs, memory dumps, checkpoints | `runtime/` (gitignored) — **never** under `src/` |

Full rules: [docs/CODING_PRINCIPLES.md](docs/CODING_PRINCIPLES.md).

---

## Style checklist (short)

- [ ] Descriptive names (`snake_case` modules/functions, `PascalCase` classes, `UPPER_SNAKE_CASE` constants)
- [ ] Booleans prefixed: `is_`, `has_`, `can_`, `should_`
- [ ] Line length ≤ 100; prefer functions ≤ ~40 lines
- [ ] Single responsibility; no new `utils/` / `helpers/` packages
- [ ] No dead code left behind
- [ ] Tests updated for behavior changes
- [ ] Docs updated when behavior or status changes (README / STATUS / dataset README)
- [ ] Package nesting stays within 3–4 levels under `incident_agent`

---

## Commit messages

Prefer short, imperative summaries focused on **why**:

- `Add OOM fixtures to CrashLoop synthetic dataset`
- `Raise confidence threshold for scale_deployment actions`
- `Penalize over-mutation in remediation_safety metric`

Avoid mixing unrelated refactors with feature work. Split PRs when a change spans many layers.

---

## Pull requests

Include:

1. **Which contribution path** (scenarios / SRE review / simulations / metrics / safety)  
2. **Which layer** the change belongs to  
3. **How you tested** (`pytest`, CLI, or “docs-only”)  
4. **Risk notes** if you touch execution policy or allowlisted actions  
5. Any follow-ups (“still stubbed…”, “needs live-cluster validation”)

Do **not** commit:

- Generated JSONL under `data/synthetic/` (unless intentionally tiny fixtures for tests)  
- Benchmark `artifacts/` dumps  
- Anything under `runtime/` except intentional placeholder docs  
- Secrets or private cluster details  

---

## Reporting issues

Use GitHub Issues with a clear label in the title when possible:

- `scenario: …` — new or broken incident fixtures  
- `SRE review: …` — workflow / process feedback  
- `sim: …` — failure simulation ideas  
- `eval: …` — metric bugs or proposals  
- `safety: …` — policy / allowlist concerns  
- `bug: …` — unexpected agent behavior  

Security-sensitive reports (ways to bypass the execution gate): mark clearly and avoid publishing exploit details in public Issues if impact is severe — contact maintainers directly when possible.

---

## Code of collaboration

- Assume good intent; prefer precise technical critique over vibe checks  
- Safety and eval disagreements are welcome — bring examples  
- Small PRs merge faster than epic rewrites  
- Docs-only and fixture-only PRs are first-class contributions  

Welcome aboard.
