# 0022 Explicit Europe PMC OA Acquisition

## Decision

Add `discovery acquire` as the only create-only source-write route. Exact `actor: user`, one persisted candidate ID, a live `auto_acquisition_eligible` Europe PMC resolution and an existing uniquely addressable `local_inbox` are required.

The route streams one bounded PDF into an exclusive same-directory partial, validates status/media type/signature/hash/size and `pdfplumber` preflight, then publishes `<candidate_id>.pdf` with an exclusive hard link during the prepared phase of the candidate-store transaction. Existing source files are never replaced.

## State

The existing discovery-candidate schema remains version `1.0`. `not_started` records remain valid without migration. A successful operation sets `acquisition_status: acquired` and adds one receipt containing the opaque provider asset, resolution context, portable source reference, SHA-256, size, content type and timestamp. The candidate remains `metadata_only` and `not_evidence: true` until a later Registry operation.

## Failure Boundary

Ordinary pre-replacement failure may remove only files created by that same operation while device/inode, size and hash still match. Crash or ambiguous states remain in place and are reported by Guardian. Core never adopts or deletes an unreceipted final or partial automatically.

Acquisition stops before Registry, Parse, Paper Card, Evidence, Question Mapping and Step 7. A formal publication never overwrites or deletes a preprint.
