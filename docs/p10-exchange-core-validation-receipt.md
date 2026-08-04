# P10 Exchange Core Validation Receipt

Date: 2026-08-04

## Scope

Validated the merged P10 Core Exchange head
`f655037c27ad681e57be8f322eee481efccd2188` against baseline
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

Final full-suite validation on the merged source snapshot:

```text
1083 passed, 4 skipped
```

The four skips are the existing Windows-hosted POSIX permission cases.

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
wheel: research_kb_core-0.1.0-py3-none-any.whl
wheel_sha256: 89cfdcb776a68786803e90e06359d97d98e764fb10e04a7fcbef8c70a303081d
sdist: research_kb_core-0.1.0.tar.gz
sdist_sha256: 80cc30da722d8bff3d747a82a0be0f825f950b59cd20735ef1f9aad1eb660ab4
```

The wheel contains the P10 Exchange runtime and all registered Exchange schemas, including
`exchange-local-export-receipt`. The reviewed feature-branch and merged-head wheel builds
were byte-identical.

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

Core P10 Deliveries A/B are merged and closed at the final head above. App Delivery C must
pin the exact merged Core commit and wheel digest before accepting Exchange custody or UI
validation as complete.
