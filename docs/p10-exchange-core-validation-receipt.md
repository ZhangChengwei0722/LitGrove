# P10 Exchange Core Validation Receipt

Date: 2026-08-04

## Scope

Validated the P10 Core Exchange deliveries on `feature/p10-exchange` against baseline
`f343389dce8f3908f775cc2c5c9bdea4a56bf4fb`.

The validated surface includes:

- paper, question, direction and workspace source-free export closure;
- explicit rights-asserted source-inclusive export for current registered PDF assets;
- canonical deterministic archive serialization and durable local export receipts;
- safe archive inspection, compatibility classification and immutable external-origin import;
- workspace writer locking, import recovery and Guardian package/journal integrity checks;
- Application Service interface `1.18` and thin CLI adapters.

No private scientific workspace, real PDF, real Obsidian vault, legacy migration or embedded
Agent runtime was opened or modified.

## Test Results

Final unit shards on the same source snapshot:

```text
a-f: 366 passed
g-l: 95 passed, 2 skipped
m-r: 313 passed
s-z: 147 passed, 2 skipped
total: 921 passed, 4 skipped
```

The four skips are the existing Windows-hosted POSIX permission cases.

Additional groups:

```text
contract:    97 passed
integration: 44 passed
privacy:      4 passed
benchmark:   16 passed
```

Combined deterministic pytest coverage:

```text
1082 passed, 4 skipped
```

Additional validation:

```text
compileall: success
git diff --check: success
privacy scan: success, 7 expected fixture findings, 0 unexpected findings
pip check: no broken requirements
base installed-wheel smoke: success
PDF-extra installed-wheel smoke: success
wheel Exchange payload/schema inspection: success
```

## Exact Wheel

```text
file: dist/research_kb_core-0.1.0-py3-none-any.whl
sha256: a4651aa0813d940a744f9e423c01ace00ce8e54113646a9a6129eab537f2c19e
```

The wheel contains the P10 Exchange runtime and all registered Exchange schemas, including
`exchange-local-export-receipt`.

## Security And Authority Assertions

- Imported records remain `external_unreviewed` under `unsigned_external_claims`.
- Claimed external `verified` status does not become local factual authority.
- Source-inclusive export fails closed on missing, stale, changed, non-PDF or unsafe assets.
- Archive import rejects traversal, links, encryption, unsupported compression, path
  collisions and every frozen safe-reader budget class.
- Registry and canonical scientific stores remain unchanged by import.
- Exchange stages and packages remain confined to P10-managed workspace paths.
- Export/import mutations serialize under the workspace writer lock.

## Result

Core P10 Deliveries A/B are ready for commit and non-destructive local merge. App Delivery C
must pin the exact merged Core wheel before implementing upload/download custody and the
Exchange work surface.
