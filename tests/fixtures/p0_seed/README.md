# P0 Synthetic Seed Fixture

This fixture is entirely `synthetic_from_scratch` and contains no real paper text, PDF, private path or future record type.

## Materialized Baseline

- one `workspace` and one `domain-profile` using existing contract `1.0`;
- one fabricated Primary registry/parse/Evidence/review-queue/Paper Card route;
- one fabricated Review registry/parse/Review Memory route;
- one existing Question Mapping and one existing Step 7 Insight candidate;
- existing process-event and Guardian records;
- two tiny read-only synthetic text assets.

Not materialized: Source Adequacy, Pipeline Job, Agent Task, staging, Direction, Field Map, Tag, Exchange conflict, generated-view freshness or backup records.

`seed-bundle.json` is the authoritative cross-record validation fixture. `workspace.yaml` and `domain-profile.yaml` are readable copies of the corresponding records for future workspace-bootstrap tests; they do not introduce a new contract.

## Validation

From the repository root:

```powershell
@'
import json
from pathlib import Path
from research_kb.contracts.validator import validate_bundle

path = Path("tests/fixtures/p0_seed/seed-bundle.json")
bundle = json.loads(path.read_text(encoding="utf-8"))
diagnostics = validate_bundle(bundle, actor="cli")
assert diagnostics == [], [item.to_dict() for item in diagnostics]
'@ | .\.venv\Scripts\python.exe -

.\.venv\Scripts\python.exe -m research_kb privacy scan --root .
```
