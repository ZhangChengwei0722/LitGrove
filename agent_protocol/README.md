# Agent Protocol

The Agent layer performs semantic reading and candidate generation, then submits structured records to the CLI boundary. It must not allocate final IDs, invent provenance, use review-queue boundaries as evidence, assign human-only states, or directly maintain both JSONL and Markdown.

The Portable Agent Skill is deferred. This directory currently records only the shared responsibility boundary.

For M2A-2 compatibility work, a private integration may construct a `LegacyReaderAdapter` and pass it explicitly to the CLI composition seam. The Agent may interpret the resulting difference report, but it must not treat that report as canonical evidence, infer that migration has occurred, or bypass blocking differences. Shared Core never discovers or imports private adapters on its own.
