# Contributor Guide

## Change Process

1. Start from an issue or approved implementation plan.
2. Keep one bounded behavior or contract change per branch.
3. Add or update deterministic tests.
4. Run the full local suite and privacy scan.
5. State compatibility, tested platform, fixture scope, and known limits in the review description.

Release-resource smoke after `python -m build`:

```powershell
python tests/wheel_smoke.py
```

Schema, state, ID, path, and directory-protocol changes require both collaborators' review.

## Fixture Rules

- Author all names, text, measurements, identifiers, and relationships from scratch.
- Add `fixture_origin: synthetic_from_scratch` where the schema permits it.
- Never derive fixtures by editing private research records.
- Intentional privacy failures must be exact-file allowlisted with an expected code and count.

## Platform Rules

Tests must include Windows-shaped and POSIX-shaped paths independent of the host. Persisted relative paths always use `/`.
