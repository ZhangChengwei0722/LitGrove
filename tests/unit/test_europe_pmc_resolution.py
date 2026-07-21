from __future__ import annotations

import pytest

from research_kb.discovery.europe_pmc import EUROPE_PMC_ENDPOINT, EuropePmcResolver
from research_kb.errors import ResearchKBError


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, *, endpoint, params, timeout_seconds):
        self.calls.append((endpoint, params, timeout_seconds))
        return self.payload


def candidate(*, doi="10.0000/synthetic.discovery", sources=None):
    return {
        "candidate_id": "discovery_a1111111-1111-4111-8111-111111111111",
        "result_key": (
            f"doi:{doi}"
            if doi is not None
            else "europe-pmc:med:synth-discovery-1"
        ),
        "doi": doi,
        "discovery_sources": sources
        or [
            {
                "provider": "europe-pmc",
                "source": "MED",
                "record_id": "SYNTH-DISCOVERY-1",
            }
        ],
    }


def payload(*items, hit_count=None):
    return {
        "version": "synthetic-6.9",
        "hitCount": len(items) if hit_count is None else hit_count,
        "resultList": {"result": list(items)},
    }


def oa_item(*, pmcid="PMC1234567", source="MED", record_id="SYNTH-DISCOVERY-1"):
    return {
        "source": source,
        "id": record_id,
        "doi": "10.0000/synthetic.discovery",
        "pmcid": pmcid,
        "isOpenAccess": "Y",
        "inEPMC": "Y",
        "hasPDF": "Y",
        "fullTextUrlList": {
            "fullTextUrl": [
                {
                    "availabilityCode": "OA",
                    "documentStyle": "pdf",
                    "site": "Europe_PMC",
                    "url": f"https://europepmc.org/articles/{pmcid}?pdf=render",
                }
            ]
        },
    }


def test_resolver_uses_exact_doi_query_and_normalizes_one_oa_asset() -> None:
    transport = FakeTransport(payload(oa_item()))

    result = EuropePmcResolver(transport=transport).resolve(candidate())

    endpoint, params, timeout_seconds = transport.calls[0]
    assert endpoint == EUROPE_PMC_ENDPOINT
    assert params == {
        "query": 'DOI:"10.0000/synthetic.discovery"',
        "format": "json",
        "resultType": "core",
        "pageSize": 100,
    }
    assert timeout_seconds == 20
    assert result.resolution_status == "auto_acquisition_eligible"
    assert result.lookup_identity == {
        "kind": "doi",
        "doi": "10.0000/synthetic.discovery",
    }
    assert result.provider_asset_ref is not None
    assert result.provider_asset_ref.to_dict() == {
        "provider": "europe-pmc",
        "source": "MED",
        "record_id": "SYNTH-DISCOVERY-1",
        "pmcid": "PMC1234567",
        "asset_kind": "pdf",
        "route": "europe-pmc-pdf-v1",
    }
    assert result.access_basis == "repository_open_access"
    assert result.license_observation == "provider_oa_policy_no_license_text"
    assert result.manual_reason is None


def test_resolver_without_doi_uses_lexically_first_exact_source_identity() -> None:
    transport = FakeTransport(
        payload(oa_item(source="MED", record_id="A-RECORD"))
    )
    selected = candidate(
        doi=None,
        sources=[
            {"provider": "europe-pmc", "source": "PMC", "record_id": "Z-RECORD"},
            {"provider": "europe-pmc", "source": "MED", "record_id": "A-RECORD"},
        ],
    )

    result = EuropePmcResolver(transport=transport).resolve(selected)

    assert transport.calls[0][1]["query"] == 'EXT_ID:"A-RECORD" AND SRC:"MED"'
    assert result.lookup_identity == {
        "kind": "source",
        "source": "MED",
        "record_id": "A-RECORD",
    }


def test_resolver_collapses_duplicate_rows_for_the_same_pmcid() -> None:
    first = oa_item(source="PMC", record_id="PMC1234567")
    second = oa_item(source="MED", record_id="SYNTH-DISCOVERY-1")
    transport = FakeTransport(payload(first, second))

    result = EuropePmcResolver(transport=transport).resolve(candidate())

    assert result.resolution_status == "auto_acquisition_eligible"
    assert result.provider_asset_ref is not None
    assert result.provider_asset_ref.pmcid == "PMC1234567"
    assert result.provider_asset_ref.source == "MED"


def test_resolver_requires_manual_review_for_multiple_distinct_oa_assets() -> None:
    transport = FakeTransport(payload(oa_item(), oa_item(pmcid="PMC7654321")))

    result = EuropePmcResolver(transport=transport).resolve(candidate())

    assert result.resolution_status == "manual_review_required"
    assert result.provider_asset_ref is None
    assert result.access_basis == "repository_open_access"
    assert result.manual_reason == "multiple_assets"


@pytest.mark.parametrize(
    ("availability_code", "expected_status", "expected_basis", "expected_reason"),
    [
        ("F", "manual_review_required", "public_free_to_read", "ambiguous_access"),
        ("S", "institutional_browser_required", "institutional", "subscription_only"),
    ],
)
def test_resolver_classifies_non_oa_pdf_access_without_exposing_an_asset(
    availability_code,
    expected_status,
    expected_basis,
    expected_reason,
) -> None:
    item = oa_item()
    item["isOpenAccess"] = "N"
    item["fullTextUrlList"]["fullTextUrl"][0]["availabilityCode"] = availability_code
    transport = FakeTransport(payload(item))

    result = EuropePmcResolver(transport=transport).resolve(candidate())

    assert result.resolution_status == expected_status
    assert result.provider_asset_ref is None
    assert result.access_basis == expected_basis
    assert result.manual_reason == expected_reason


def test_resolver_reports_no_matching_record_and_no_supported_pdf_route() -> None:
    unmatched = oa_item()
    unmatched["doi"] = "10.0000/other"
    no_match = EuropePmcResolver(transport=FakeTransport(payload(unmatched))).resolve(candidate())
    assert no_match.resolution_status == "no_supported_oa_route"
    assert no_match.manual_reason == "no_matching_record"

    item = oa_item()
    item["isOpenAccess"] = "N"
    item["inEPMC"] = "N"
    item["hasPDF"] = "N"
    item["fullTextUrlList"] = {"fullTextUrl": []}
    no_route = EuropePmcResolver(transport=FakeTransport(payload(item))).resolve(candidate())
    assert no_route.resolution_status == "no_supported_oa_route"
    assert no_route.manual_reason == "no_pdf_route"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update({"isOpenAccess": "maybe"}),
        lambda item: item["fullTextUrlList"]["fullTextUrl"][0].update(
            {"url": "https://example.invalid/articles/PMC1234567?pdf=render"}
        ),
        lambda item: item.update({"pmcid": "1234567"}),
    ],
)
def test_resolver_rejects_malformed_or_inconsistent_provider_output(mutate) -> None:
    item = oa_item()
    mutate(item)

    with pytest.raises(ResearchKBError) as error:
        EuropePmcResolver(transport=FakeTransport(payload(item))).resolve(candidate())

    assert error.value.diagnostic.code == "RKBC-033"

def test_resolver_rejects_more_than_one_bounded_page() -> None:
    with pytest.raises(ResearchKBError) as error:
        EuropePmcResolver(transport=FakeTransport(payload(hit_count=101))).resolve(candidate())

    assert error.value.diagnostic.code == "RKBC-033"
