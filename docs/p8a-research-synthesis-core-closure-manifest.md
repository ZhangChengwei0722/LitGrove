# P8-A Research Synthesis Core Closure Manifest

- status: `validated_pending_remote_closure`
- baseline: `16013d5c58cf4f592129bb93f07425adf522b32d`
- branch: `feature/p8-research-synthesis`
- plan_commit: `89c301b`
- implementation_commit: `pending_pre_commit`
- merge_commit: `pending`
- validation_receipt: `docs/p8a-research-synthesis-core-validation-receipt.md`
- next_local_phase: `P8-B localhost App Research Synthesis workspace`

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

1. Commit the reviewed P8-A implementation and durable validation records.
2. Push `feature/p8-research-synthesis`, create one focused PR and review the remote diff.
3. Merge the PR, fast-forward local `main` and run post-merge affected validation.
4. Rebuild the exact merged-head Core wheel and record its digest before P8-B pins Core.

GitHub unavailability does not authorize an approximate App pin. Local P8-B planning may
continue, but installed-package acceptance requires the exact merged Core wheel.

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
