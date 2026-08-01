# P4-D0 Agent Handoff Inspection And Recovery Validation Receipt

- status: `passed`
- validated_at: `2026-08-01T23:12:40+08:00`
- implementation_commit: `802a53f591b985ad73568f806dc415c9266aa81f`
- implementation_tree: `eb8aed4934ffb51df9888c2342e4f2d98e707c0e`
- branch: `feature/p4d-agent-handoff-inspection`
- package: `research-kb-core==0.1.0`
- application_service_interface: `1.5`
- agent_task_registry: `p4c-v1`
- wheel_sha256: `4664ad887bba2c93ae1407e5915a8239f37835c444bfdafdf7087c63ecf2b567`
- sdist_sha256: `3172dc9087bee13d1106e1833bfdee08e732978daa7230f3e60f165d22f9f512`
- fixture_scope: `synthetic sources and generated workspaces only`

## Validation Matrix

| Check | Result |
|---|---|
| Agent Task focused regression | `20 passed` |
| deterministic intake focused regression | `17 passed` |
| complete Windows suite | `868 passed, 4 expected POSIX skips` |
| `compileall` | passed |
| source build of sdist and wheel | passed |
| isolated installed-wheel base smoke | passed |
| isolated installed-wheel PDF-extra smoke | passed after one network-only retry |
| package version | `research-kb 0.1.0` |
| privacy scan | `7 expected, 0 unexpected` |
| `git diff --check` | passed |

The first PDF-extra smoke attempt could not resolve cached dependencies while the
package index was temporarily unreachable. It did not execute product code and is not
counted as a pass. The unchanged command was retried once; dependency resolution and the
complete isolated PDF-extra smoke then passed.

## Behavior Evidence

- a current created or leased Task exposes the exact privacy-filtered payload, result
  contract, content classes and byte facts with zero writes;
- inspection returns neither prompt nor lease and never exposes a workspace path or raw
  source document;
- wrong executor, stale input basis and incorrect state CAS fail closed;
- current leased-state replay returns the identical manifest and lease with zero writes;
- predecessor-state replay remains compatible for a lost first prepare response;
- changed handoff content or an invalid leased state digest cannot be recovered;
- route, Primary and Review staging/approval behavior remains compatible;
- no CLI command, HTTP endpoint, embedded Agent runtime, credential path, scheduler,
  private workspace or real PDF was introduced or accessed.
