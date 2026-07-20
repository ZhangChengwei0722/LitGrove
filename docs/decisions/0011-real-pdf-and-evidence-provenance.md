# ADR 0011: Real PDF Parsing And Evidence Provenance

- status: accepted_for_m3a_0a

## Decision

Shared Core provides one explicit real-PDF adapter, `pdfplumber`, through the optional `research-kb-core[pdf]` dependency. The adapter is deterministic infrastructure: it verifies a regular PDF source and binary signature, extracts one text record per PDF page with fixed options, normalizes line endings to LF, reports the exact installed `pdfplumber` package version, and never performs OCR, network access, subprocess execution, or source writes.

The CLI selects `synthetic-text` or `pdfplumber` explicitly. It does not auto-detect or silently substitute an adapter. A missing PDF extra returns `RKBC-028`; malformed, encrypted, image-only, all-empty, or otherwise unsupported PDF sources return `RKBC-029` with bounded diagnostics.

Real-PDF parsed pages use `page:<pdf_page>:text`. Canonical Evidence against those pages uses `page:<pdf_page>:char:<start>-<end>`, where offsets are zero-based and end-exclusive over the stored Python string. The Evidence quote must equal that exact slice. Existing invented fixtures may retain `page:<pdf_page>:block:<block_number>` only when the whitespace-normalized quote occurs in the same paper's linked page text.

## Provenance And Storage

Bundle validation requires each paper's active page rows to be in ascending page order, have unique positive PDF page numbers, and share one parse run and parser identity. Every Evidence record must resolve to a page owned by the same paper, match its source-page number and supported locator, and retain the Registry source fingerprint.

The same validator runs during temporary Evidence promotion, stored-bundle validation, Guardian, and read operations that validate a complete workspace. Evidence promotion also rechecks source SHA-256 before temporary validation and after target replacement. A post-replacement source change enters transaction `needs_resolution` and emits no success event.

No schema, workspace layout, source-copy mechanism, or parse manifest is added. Registry source identity remains immutable for one paper ID. A changed or replacement source must use a separately governed registration path rather than rewriting provenance beneath existing records.

## Limits

This decision does not support OCR, geometric PDF coordinates, figures, tables, supplementary files, non-contiguous excerpts, Review Card or Review Memory processing, Step 7 runtime, document-type classification, or Portable Skill orchestration. Parser output can change across package versions, so exact adapter/version identity remains part of every parsed-page record.
