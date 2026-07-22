# 0025: Read-Only Manuscript Projection

## Decision

Add one workspace-bound read command:

```text
research-kb manuscript inspect --workspace <config> --source <absolute.docx|absolute.pdf>
```

Core confines the source to exactly one declared root, fingerprints it before and after extraction, and emits bounded stable paragraph or page units with parser identity and coverage limits. DOCX uses a fixed standard-library OOXML reader; PDF reuses the installed `pdfplumber` extraction policy. The complete report is written once to stdout with `persistent_writes: 0`.

## Boundary

The projection is private task context, not canonical knowledge. It creates no manuscript store, schema, ID, event, journal, cache, Registry record or Markdown view. The Portable Skill stops after projection and does not claim semantic audit, claim extraction, evidence matching or rewriting.

Unsupported manuscript content returns `RKBC-035`; a missing PDF extra remains `RKBC-028`. The source, OOXML archive, unit count and extracted text are explicitly bounded, and source changes invalidate the operation.

## Consequences

M3D-1 can later design purpose-specific manuscript audit against a stable deterministic input without coupling semantic judgment to file parsing. M3D-0A itself cannot select audit criteria, query the knowledge base, persist a claim map or alter a manuscript.
