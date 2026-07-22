# 0027 Additive PdfPlumber Text-Flow Profile

Status: accepted

## Decision

Keep the existing `pdfplumber` spatial extraction profile unchanged and add an explicit `pdfplumber-text-flow` adapter for new primary/review intake. The new profile fixes `x_tolerance` at `1`, `y_tolerance` at `3`, `layout` at `False` and `use_text_flow` at `True`.

The distinct adapter name is part of parser identity; both adapters report the exact installed `pdfplumber` package version. Capability output lists both from one lazy optional-dependency probe, and the CLI never auto-detects or silently substitutes either profile.

## Consequences

Text-flow can preserve logical column order when the PDF content stream is authored in reading order and can avoid word joining caused by the legacy tolerance. It does not reconstruct arbitrary layout, figures, tables or OCR. Agents must inspect `parse show` and stop scientific promotion when reading order remains ambiguous.

This decision adds no schema, ID namespace, workspace layout, source write, migration or private-workspace behavior. Historical `parser.adapter: pdfplumber` records retain their original meaning.
