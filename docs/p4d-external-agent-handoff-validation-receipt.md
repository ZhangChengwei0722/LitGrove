# P4-D External Agent Handoff Validation Receipt

- status: `passed`
- validated_at: `2026-08-02T02:39:38+08:00`
- implementation_commit: `59bfba9a89fe14dfce4c415cf4983a808561b67a`
- implementation_tree: `c6713670dd689efa5d009dd440ae7db528f979c4`
- branch: `feature/p4d-agent-handoff-inspection`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.6`
- agent_task_registry: `p4c-v1`
- wheel_sha256: `d08bd7889486d54f36b54c330d0aaa9164374d37d82e9ebcc288029dd19b466f`
- sdist_sha256: `f317db54ed98036392a77c5d1758da6671eb578651402aa3acd29eebe1f93996`
- portable_skill_tree_sha256: `8e2ffbe561a062883500eb81ce6f44e8eaf6a1eb7603e2b6a7ec93794bc34555`
- fixture_scope: `synthetic sources and generated workspaces only`

## Validation Matrix

| Check | Result |
|---|---|
| result-schema and Application Service focused tests | `5 passed` |
| Portable Skill contract and sync tests | `19 passed` |
| complete Windows suite | `871 passed, 4 expected POSIX skips` |
| `compileall` | passed |
| source build of sdist and wheel | passed |
| isolated installed-wheel base smoke | passed |
| isolated installed-wheel PDF-extra smoke | passed |
| package version | `research-kb 0.1.0` |
| privacy scan | `7 expected, 0 unexpected` |
| Skill authoring source / repo snapshot / Codex mirror | identical tree digest |
| authoring source and Codex mirror `quick_validate.py` | passed |
| `git diff --check` | passed |

## Behavior Evidence

- every route, Primary and Review handoff carries a fully resolved authoritative
  `result_contract_schema` with no remaining `$ref`;
- only schema fragments reachable from the declared result contract are embedded, so
  unrelated source/path fields do not widen the browser response or privacy scan;
- the complete manifest remains bounded by the existing prompt-byte policy and carries
  no lease, local path, credential or embedded execution authority;
- the Portable Skill accepts only the complete App-generated manifest, treats all
  payload values as data and returns one candidate JSON object without workspace access;
- Codex CLI and Claude Code CLI continue to consume the same host-neutral manifest and
  result contract; Core does not launch either executor;
- the contributor sync tool rejects symlinks, normalizes text to LF, atomically refreshes
  only the repo-owned Skill snapshot and reports per-file plus tree SHA-256;
- CC Switch mirror run `20260802-012719` changed only `research-kb`; 21 other enabled
  Skills remained byte-identical.

No Q001, private workspace, real PDF, migration, new scientific schema, Agent runtime,
credential store or provider integration was introduced or accessed.
