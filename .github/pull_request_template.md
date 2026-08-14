## Summary

Describe the bounded change and why it is needed.

## Impact

- Public contracts, schemas, IDs, paths, or directory layout changed: no
- Source-write or user-authority boundary changed: no
- Compatibility or migration impact: none

## Privacy And Fixtures

- [ ] No private research data, credentials, private paths, or real source documents were added.
- [ ] New fixtures, if any, are authored from scratch and marked `synthetic_from_scratch` where supported.
- [ ] Existing source assets remain immutable.

## Validation

- [ ] Targeted tests passed, or no targeted test was needed for this change.
- [ ] Risk-appropriate L0-L4 validation passed; high-risk changes include complete L3 and L4.
- [ ] `python -m build`
- [ ] `python tests/wheel_smoke.py`
- [ ] `python tests/wheel_pdf_smoke.py`
- [ ] `python -m research_kb --version`
- [ ] `python -m research_kb privacy scan --root .`
- [ ] `Dependency review` and `Python dependency audit`
- [ ] `CodeQL`
- [ ] User-visible, compatibility, deprecation, or security changes are recorded in `CHANGELOG.md`, or not applicable.

## Known Limits

List any platform, dependency, or validation limits. Use `none` when there are none.

## Review Governance

This repository uses the accepted single-maintainer exception: required approvals remain `0`,
while required checks, administrator enforcement, conversation resolution, and branch
force-push/deletion protections remain active.
