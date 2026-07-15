# ADR 0007: Config-First Workspace Bootstrap

- status: accepted_for_m2a_1

## Decision

Runtime commands require an existing workspace config, a valid domain profile, a safe managed layout, and a matching `.research-kb/workspace.json` identity marker. Only `research-kb workspace init --workspace <workspace.yaml> [--dry-run]` may inspect an unbound config and create the approved managed scaffold.

One shared semantic validator enforces duplicate and colliding source roots, source availability, knowledge/source/inbox separation, managed-name normalization, known-content rules, link/reparse safety, POSIX modes, and marker identity. Runtime callers cannot disable initialized-workspace enforcement.

Apply mode performs read-only preflight, creates the minimum private lock scaffold, acquires the workspace lock, repeats mutation-sensitive preflight, creates remaining managed directories, then atomically writes and verifies the marker. Concurrent matching configs converge on one `initialized` result and one `no_change`; conflicting configs cannot replace the winning marker.

The marker contains no timestamp, absolute path, scientific content, canonical ID allocation, or review state. Resolved path identities influence only its SHA-256 config fingerprint.

## No Process Event

Bootstrap creates operational structure, not scientific state. It therefore creates no canonical JSON/JSONL store, transaction journal, process event, or Guardian report. The marker uses an atomic same-directory write and read-back check outside the canonical transaction kernel.

A markerless populated M1B store may receive a marker only after every existing structured record and completed transaction validates. Adoption never edits an existing canonical record to make validation pass.

## Rejected Alternatives

Lazy directory creation alone cannot bind a managed root to one config. A stateless `ensure-layout` command permits conflicting configs to claim the same root. Emitting a synthetic process event would falsely represent operational setup as a canonical research mutation.
