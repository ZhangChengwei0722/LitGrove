# Pilot exFAT Publish Remediation Implementation Plan

- date: `2026-08-05`
- defect: `PILOT-DEFECT-001`
- branch: recorded by the Git delivery receipt
- base: `d646c052e6a62c237e6392b9832555f571e2428c`
- scope: bounded Core runtime remediation
- schema_change: false
- application_service_interface_change: false (`1.18` retained)

## Objective

Restore create-only local PDF intake on Windows filesystems that reject hard links, and
allow the owning Pipeline Job to publish an already receipted operation-owned partial
without requiring the browser to upload the source bytes again.

## Invariants

- Keep hard-link publication as the preferred path.
- Use same-directory `os.rename` only on Windows when `os.link` fails with
  `winerror` `1` or `50`.
- Never use `os.replace` or another overwrite-capable fallback.
- Fail closed when the destination exists or appears during publication.
- Revalidate partial identity before publication and final identity plus SHA-256 after it.
- Recover only one partial whose Job, receipt, destination, digest and size close exactly.
- Do not create a second Source Asset receipt or require the original upload stream.
- Do not access a protected legacy workspace, legacy CLI data or untouched Pilot cases
  during remediation.

## Work

1. Add focused failing tests for Windows unsupported-hardlink fallback, destination race,
   unexpected hard-link errors, rename failure and same-Job streamless recovery.
2. Add a receipt-bound `LocalSourceIntakeService` recovery operation and call it from
   `DeterministicIntakeApplicationService.resume()` before Registry reconciliation.
3. Preserve current hard-link publication and crash-recovery behavior.
4. Run targeted tests, the complete Core suite, package build, wheel smoke, privacy scan
   and diff checks.
5. Merge the Core fix, rebuild the exact wheel, update the App compatibility pin, rebuild
   and validate the App, then create a new Pilot manifest revision before replay.

## Exit Gate

The remediation is implementation-complete only when the merged exact Core/App packages
can resume `case-p01` from its existing Job and partial to the semantic gate, with no
Guardian finding from the interrupted copy. This gate does not authorize opening untouched
Pilot cases; those remain blocked until the diagnostic replay closes.
