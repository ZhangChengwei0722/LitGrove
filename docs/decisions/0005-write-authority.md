# ADR 0005: Write And Authority Boundary

- status: accepted_for_m1b

Agent output is candidate input. The CLI validates, normalizes, and atomically promotes only supported mutation requests. Agents cannot assign human-only review states, final screening decisions, CLI-owned IDs, automation results, source fingerprints, or high-risk source operations.

The internal actor value `stored` validates already persisted canonical records, including legitimate human-only states. It is not accepted by public CLI actor options and does not authorize a mutation. Replacing a human-reviewed Paper Card, Evidence, or review queue record is user-only; Registry replacement may preserve an existing human-only state when that field is omitted.
