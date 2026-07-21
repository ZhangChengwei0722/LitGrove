# 0024: Continue Acquired Candidates Through Existing Intake

## Decision

A separately authorized `acquired_candidate_intake` task resumes the existing local intake workflow after `intake inspect-acquired` and Registry return one current paper ID. It stops after Registry only for an explicit `registry_only` request; otherwise it continues through status, Parse, primary/review routing and Guardian. Step 7 remains separately explicit.

## Rationale

M3C-2C already closes the deterministic receipt-to-Registry handoff. Registry, real-PDF Parse, Paper Card/Evidence, Review Memory, Question Mapping and Guardian runtimes already accept the resulting paper ID. A second workflow runtime or persisted candidate-to-paper link would duplicate authority and state.

## Boundaries

`discovery acquire` still stops before intake. Acquisition, intake and Step 7 authority remain distinct. Provider paper type is metadata only. No schema, layout, capability, Core service, workflow store or source operation changes in this decision.
