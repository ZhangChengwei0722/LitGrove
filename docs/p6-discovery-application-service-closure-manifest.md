# P6 Discovery Application Service Closure Manifest

- closed_at: `2026-08-03`
- branch: `feature/p6-discovery-acquisition`
- baseline: `main@9efa9f1bfe125fae72bed4cebf97f061b6d4a7f3`
- application_service_interface: `1.10`
- validation: `docs/p6-discovery-application-service-validation-receipt.md`
- status: `closed_for_app_integration`

## Delivered

- one exported `DiscoveryApplicationService` over existing discovery contracts;
- fixed Europe PMC production connector, resolver and acquisition transport;
- transient search, explicit user selection, paginated candidate reads, zero-write OA
  resolution, explicit acquisition and read-only acquired-candidate handoff;
- capability declaration and Application Service compatibility advancement to `1.10`;
- deterministic fake-network tests, complete regression shards and fresh wheel smoke.

## Not Delivered

- no automatic Registry, Parse or semantic continuation after acquisition;
- no manual browser/institutional acquisition, arbitrary URL or credential handling;
- no second provider, version preference, correction/retraction propagation or fuzzy
  identity merge;
- no App UI, Exchange, Obsidian, Research Synthesis or migration access.

The exact reviewed wheel digest is recorded in the validation receipt. The App must pin
that wheel and the eventual Core commit before packaged validation.
