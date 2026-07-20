# Agent Protocol

The Agent layer performs semantic reading and candidate generation, then submits structured records to the CLI boundary. It must not allocate final IDs, invent provenance, use review-queue boundaries as evidence, assign human-only states, or directly maintain both JSONL and Markdown.

The Portable Agent Skill is deferred. This directory currently records only the shared responsibility boundary.

For M2A-2 compatibility work, a private integration may construct a `LegacyReaderAdapter` and pass it explicitly to the CLI composition seam. The Agent may interpret the resulting difference report, but it must not treat that report as canonical evidence, infer that migration has occurred, or bypass blocking differences. Shared Core never discovers or imports private adapters on its own.

For M2B-1 Question Mapping, the Agent submits selected Paper Card Unit IDs, paper roles, relevance rationales, and optional question-specific review queue boundaries. It must not submit question/link IDs or evidence projections. Use `question_origin: user_supplied` for a directly supplied active question, `user_approved_candidate` only after explicit approval, and `existing_question` only for refresh. Unapproved generated questions remain in the task report.

Question roles and rationales are candidate interpretation, not evidence. If a Card Unit, evidence record, or queue boundary is wrong, correct that upstream record through its own authority flow rather than editing a mapping projection by hand.

For M3A-0A, the Agent may explicitly request `parse run --adapter pdfplumber` when the optional PDF capability is installed. It must select Evidence quotes from the stored normalized page text and submit `page:<n>:char:<start>-<end>` locators that reproduce the quote exactly. It must not calculate offsets from a separate full-text copy, invent block boundaries for real PDFs, trigger OCR fallback, or treat figure/table interpretation as page-text evidence.

`RKBC-028` means the local PDF extra is unavailable; `RKBC-029` means the selected PDF is unsupported by this text-only adapter. Both are stop boundaries for the current operation, not permission to bypass Core with a private parser. Review processing, document classification, parsed-content read commands, and the Portable Skill remain deferred.

For M2B-2, the Agent may request `question render` as a disposable reading surface. It must not edit the Markdown back into JSONL, treat the generated view as canonical knowledge, or cite review queue boundaries as evidence. Corrections still go through the owning Registry, Paper Card, evidence, queue, or Question Mapping contract.
