# Contributor Guide

## Change Process

1. Start from an issue or approved implementation plan.
2. Keep one bounded behavior or contract change per branch.
3. Add or update deterministic tests.
4. Run the full local suite and privacy scan.
5. State compatibility, tested platform, fixture scope, and known limits in the review description.

Use the repository virtual environment so the editable package and bounded dependencies are active:

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m research_kb privacy scan --root .
```

Release-resource smoke after `python -m build`:

```powershell
python tests/wheel_smoke.py
```

Schema, state, ID, path, and directory-protocol changes require explicit user approval, focused self-review, targeted tests, and the full Windows validation gate. External collaborator review is optional.

## Fixture Rules

- Author all names, text, measurements, identifiers, and relationships from scratch.
- Add `fixture_origin: synthetic_from_scratch` where the schema permits it.
- Never derive fixtures by editing private research records.
- Intentional privacy failures must be exact-file allowlisted with an expected code and count.
- Runtime fixtures must execute in a temporary copy; tests must not write canonical state into the repository fixture tree.
- Record source hashes before a runtime scenario and assert that every hash is unchanged afterward.

## Mutation Service Rules

- Resolve all targets through `WorkspaceLayout`; do not accept a direct `knowledge_root` override.
- Read current canonical state before composing a replacement and pass its digest to the transaction manager.
- Validate the temporary target before `os.replace`.
- Keep source assets outside the writable boundary.
- Emit no process event payload containing candidate scientific text.
- Add a failure, conflict, and source-immutability test for each new mutating service.

## Platform Rules

Tests must include Windows-shaped and POSIX-shaped paths independent of the host. Persisted relative paths always use `/`.

Windows is the required live acceptance platform. macOS compatibility remains a design target and may be checked when available, but a live macOS run is not a release gate unless a later approved milestone says otherwise.
