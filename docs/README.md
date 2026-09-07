# Documentation

Welcome. Start with the root [README.md](../README.md) — it is written so a new community member can understand the project in about two minutes.

| Document | Audience | Purpose |
|----------|----------|---------|
| [../README.md](../README.md) | Everyone | 2-minute overview, diagrams, status, quickstart |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Contributors | Five contribution paths, setup, PR checklist |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Contributors | Layers, providers, workflow depth |
| [LLM_BOUNDARY.md](LLM_BOUNDARY.md) | Contributors | Advisory-only LLM contract boundary |
| [SCORE_SEMANTICS.md](SCORE_SEMANTICS.md) | Contributors | Belief vs suitability vs gate score |
| [STATUS.md](STATUS.md) | Everyone | Shipped vs planned (transparent) |
| [baselines/README.md](baselines/README.md) | Contributors | Frozen baseline + Qwen/Gemma compare scorecards |
| [CODING_PRINCIPLES.md](CODING_PRINCIPLES.md) | Contributors | Naming, SoC, size limits, anti-patterns |
| [../runtime/README.md](../runtime/README.md) | Contributors | Runtime data layout (outside `src/`) |
| [../src/incident_agent/datasets/crashloopbackoff/README.md](../src/incident_agent/datasets/crashloopbackoff/README.md) | Scenario authors | CrashLoop schema, generate CLI, how to extend |
| [../progressreport1.md](../progressreport1.md) | Historical | Early Stage-0 report — **superseded** for current architecture |

## Contribution paths (quick links)

1. [Add incident scenarios](../CONTRIBUTING.md#1-add-incident-scenarios)  
2. [Review SRE workflows](../CONTRIBUTING.md#2-review-sre-workflows)  
3. [Add Kubernetes failure simulations](../CONTRIBUTING.md#3-add-kubernetes-failure-simulations)  
4. [Improve evaluation metrics](../CONTRIBUTING.md#4-improve-evaluation-metrics)  
5. [Review remediation safety policies](../CONTRIBUTING.md#5-review-remediation-safety-policies)  

When docs disagree with code, **trust the code** and open a docs PR.
