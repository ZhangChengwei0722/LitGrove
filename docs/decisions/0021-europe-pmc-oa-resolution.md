# 0021 Europe PMC OA Resolution

## Decision

Add `discovery resolve` as a candidate-scoped, read-only provider check. It accepts exactly one persisted `user_selected` candidate and the built-in `europe-pmc` provider. DOI candidates use an exact DOI query; candidates without a DOI use the lexically first stored Europe PMC source identity.

The resolver calls only the fixed Europe PMC search endpoint. It validates current OA flags and the repository PDF route, then returns an opaque `provider_asset_ref` rather than a URL. Its four routing outcomes are `auto_acquisition_eligible`, `manual_review_required`, `institutional_browser_required` and `no_supported_oa_route`.

## Boundary

Resolution writes no candidate, event, journal, cache, receipt or source file. `persistent_writes` is always zero. `auto_acquisition_eligible` records observable provider facts under the approved policy; it is not legal advice and does not authorize acquisition, redistribution, Registry intake or screening.

Multiple distinct eligible PMCIDs stop for manual review. The command has no browser, publisher, arbitrary URL, credential or fallback-provider route.

## Consequences

An acquisition command must re-resolve the candidate rather than trusting an earlier transient report. Acquisition remains a separate authority and source-write contract.
