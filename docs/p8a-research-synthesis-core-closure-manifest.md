# P8-A Research Synthesis Core Closure Manifest

- status: `closed`
- baseline: `16013d5c58cf4f592129bb93f07425adf522b32d`
- branch: `feature/p8-research-synthesis`
- plan_commit: `89c301b`
- implementation_commit: `395d0365a80580984632bbbfb5ffccdab9ecfdd5`
- merge_commit: `0142e6d96595796a525e0f79ebae157017c44e8d`
- post_merge_fix_commit: `aac28d4b96842383ce8548a2f13fa1e3e70e2ab5`
- validation_receipt: `docs/p8a-research-synthesis-core-validation-receipt.md`
- app_closure_commit: `c215560b8bc1eed688be5237bc7d64ce9a4582dd`
- next_phase: `P9 Obsidian Generated Views`

## Closed Locally

- additive Research Synthesis proposal schema, `p8-v1` Task/privacy registry and
  Application Service `1.16`;
- active-Question support closure, bounded Primary/Evidence payload and labeled Review
  background;
- four candidate types, append/replace intent, duplicate dispositions and dedicated user
  approval;
- stale-submit, replay, recovery, approval authority and direct-write replacement boundary;
- Research Synthesis read API, capability projection, Agent protocol and Portable Skill;
- complete deterministic Windows validation and installed-wheel smokes recorded in the
  validation receipt.

## Remote Closure

P8-A merged through PR #54 as `0142e6d`. App integration then exposed one active-Question
revision bug: support closure read the legacy mapping instead of its current P7 successor.
The focused correction merged through PR #55 as `aac28d4`, passed the full deterministic
Core suite and produced the exact wheel pinned by the App.

P8-B closed in the local App repository at `c215560`; development and package browser
flows, fresh-install smoke, privacy/path scans and the Portable Skill mirror all passed.
No remote closure item remains.

## Deferred Cleanup Inventory

The following tool-generated TEMP directories are retained until P11 or overall project
completion. They contain no user-authored scientific content and are regenerable from the
repository, but current lifecycle policy defers deletion:

| Path | Bytes | Role |
|---|---:|---|
| `%TEMP%/research-kb-p8a-core-wheel-candidate` | 416428 | early wheel candidate |
| `%TEMP%/research-kb-p8a-core-wheel-candidate-final` | 416428 | reviewed pre-tightening wheel candidate |
| `%TEMP%/research-kb-p8a-core-wheel-candidate-v2` | 1152287 | pre-final wheel and sdist candidate |
| `%TEMP%/research-kb-p8a-core-wheel-candidate-v3` | 1153394 | final pre-merge wheel and sdist candidate |
| `%TEMP%/research-kb-p8a-wheel-smoke-root` | 416807 | earlier installed-wheel smoke input |
| `%TEMP%/research-kb-p8a-wheel-smoke-root-final` | 416807 | later installed-wheel smoke input |

Total deferred reclaimable size: `3972151 bytes` (`3.788 MiB`). No directory was deleted.
Repository tests, fixtures, scripts, receipts, manifests and artifact digests are permanent
records rather than cleanup candidates.
