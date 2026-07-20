# Authority And Failure Boundaries

Read this file before mutation and whenever a command fails. Fail closed; never repair by bypassing Core.

## Human Authority

The Agent may create or check candidates only where the public contract permits. Never assign:

- `human_checked`;
- `verified`;
- final `included` or `excluded` screening;
- source deletion, replacement or disposition;
- migration or legacy write-freeze completion.

A generated Research Question remains report-only until the user approves it.

## Evidence Boundary

Canonical Evidence requires a current same-paper source, active parsed page, exact page/character locator and exact quote slice. Narrow every claim to what the source actually supports.

Review queue records are not evidence. Never cite them as support, count them as canonical Evidence or silently promote them.

Review processing is not implemented. Step 7 is not implemented. Stop review-like documents before primary Paper Card and Evidence promotion.

## Source And Parse Stops

Stop for:

- relative, missing, directory or out-of-root source paths;
- link or root escape;
- stale or ambiguous exact registration;
- changed source fingerprint;
- unavailable PDF adapter;
- malformed, encrypted, image-only or text-unavailable PDF;
- stale or inconsistent parse identity;
- claims requiring OCR, geometry, figures, tables, supplements or non-contiguous excerpts.

Never move, copy, rename, delete or edit a source asset.

## Integrity Stops

Stop the complete batch for:

- workspace identity or layout conflict;
- unsupported layout version;
- unresolved or incomplete transaction;
- mutation safety reported false;
- complete-bundle validation failure;
- Guardian findings that indicate shared-state integrity failure.

`transaction recover --dry-run` may explain possible actions. Do not apply recovery in this Skill.

## Semantic Stops

Stop the selected paper for:

- review, meta-analysis, perspective, commentary, protocol or low-confidence document type;
- a possible duplicate record that cannot be matched exactly;
- a claim whose wording exceeds its quote;
- an unsupported Card Unit with no valid queue representation;
- a requested Question Mapping without user supply or approval;
- an existing Card or grounded chain that would require automatic rewrite.

## Task Outcomes

Use these only in the non-canonical task report:

| Outcome | Meaning |
| --- | --- |
| `completed` | The requested M3A-1 chain newly reached read-only Guardian. |
| `unsupported_for_now` | Required runtime capability is outside M3A-1. |
| `config_required` | An existing valid workspace config was not supplied. |
| `source_stale` | Exact registration exists but source bytes changed. |
| `source_ambiguous` | More than one paper owns the exact source reference. |
| `document_type_stop` | The source is not eligible for the primary route. |
| `provenance_unavailable` | Exact page, locator or quote support cannot be produced. |
| `integrity_blocked` | Workspace, transaction or Guardian state is unsafe. |
| `needs_user_approval` | The next action requires explicit user authority. |
| `resume_available` | Current state supports a deterministic next incomplete stage. |
| `completed_no_change` | The current chain is already complete and current. |

These labels never become stored statuses.

## No Fallbacks

Do not parse workspace or domain-profile configuration. Do not read canonical JSON or JSONL files directly. Do not call a private legacy CLI, fallback parser, browser, network service or hidden script. Report the boundary and stop.
