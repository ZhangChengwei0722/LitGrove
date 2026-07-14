# Architecture

## Layers

```text
Shared Core + CLI
-> Portable Agent Skill
-> Separate private workspaces
```

Core owns deterministic contracts, validation, path and ID handling, structured I/O, status gates, logs, rendering, and Guardian checks. The Agent layer owns scientific reading, interpretation, candidate generation, and workflow decisions. Private workspaces own papers and research records.

## Knowledge Flow

```text
Source Intake -> Registry -> Parse -> Paper Card Core
-> Evidence Grounding -> Question Mapping
-> Step 7 Candidate Thinking -> Guardian / Feedback
```

Canonical evidence is the provenance backbone. Paper Card Units are the semantic entry for later reasoning. Step 7 remains candidate-level and must expand back to canonical evidence.

## Milestone 1A

This milestone implements only public contracts and validation primitives. Runtime services are intentionally absent until the contracts pass synthetic cross-domain tests.
