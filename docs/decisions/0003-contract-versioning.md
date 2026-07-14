# ADR 0003: Contract Versioning

- status: accepted_for_m1a

Use JSON Schema Draft 2020-12 and exact version `1.0`. Configs declare `contract_version`; records declare `schema_version`. Unsupported major versions fail closed. Migration is explicit and is not implemented in Milestone 1A.
