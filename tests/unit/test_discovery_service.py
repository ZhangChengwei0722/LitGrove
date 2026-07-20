from __future__ import annotations

from dataclasses import replace

import pytest

from research_kb.discovery.base import (
    DiscoveryCandidate,
    DiscoveryProviderResult,
    DiscoverySource,
)
from research_kb.errors import ResearchKBError
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.storage.json_io import serialize_json


def request(**overrides):
    value = {
        "request_version": "1.0",
        "date_from": "2026-07-14",
        "date_until": "2026-07-21",
        "title_keywords": ["targeted degradation"],
        "abstract_keywords": ["delivery"],
        "keyword_mode": "any",
        "include_preprints": True,
        "max_results": 15,
    }
    value.update(overrides)
    return value


def candidate(
    *,
    record_id: str,
    title: str,
    abstract: str,
    doi: str | None,
    first_publication_date: str = "2026-07-20",
    paper_type: str = "article",
    source: str = "MED",
    journal_or_server: str | None = "Invented Journal",
    full_text_status: str = "unknown",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        title=title,
        authors=("Alpha Researcher",),
        first_publication_date=first_publication_date,
        journal_or_server=journal_or_server,
        doi=doi,
        paper_type=paper_type,
        publication_types=("Journal Article",),
        abstract=abstract,
        discovery_sources=(DiscoverySource("europe-pmc", source, record_id),),
        full_text_status=full_text_status,
    )


class FakeConnector:
    connector_id = "europe-pmc"
    network_required = True

    def __init__(self, candidates=(), *, hit_count=None, scanned_count=None, exhausted=True):
        self.candidates = tuple(candidates)
        self.hit_count = len(self.candidates) if hit_count is None else hit_count
        self.scanned_count = len(self.candidates) if scanned_count is None else scanned_count
        self.exhausted = exhausted
        self.requests = []

    def search(self, discovery_request):
        self.requests.append(discovery_request)
        return DiscoveryProviderResult(
            provider="europe-pmc",
            provider_api_version="synthetic-1",
            provider_hit_count=self.hit_count,
            scanned_result_count=self.scanned_count,
            exhausted=self.exhausted,
            candidates=self.candidates,
        )


def service(connector: FakeConnector) -> DiscoveryService:
    return DiscoveryService(DiscoveryConnectorRegistry([connector]))


@pytest.mark.parametrize(
    "invalid",
    [
        {},
        request(request_version="2.0"),
        request(date_from="2026-07-22"),
        request(date_from="2026-06-01"),
        request(title_keywords=[], abstract_keywords=[]),
        request(title_keywords=[" "]),
        request(title_keywords=["same", "SAME"]),
        request(title_keywords=["x\nquery"]),
        request(keyword_mode="near"),
        request(max_results=0),
        request(max_results=16),
        {**request(), "unexpected": True},
    ],
)
def test_discovery_request_is_closed_and_bounded(invalid) -> None:
    with pytest.raises(ResearchKBError) as caught:
        service(FakeConnector()).search("europe-pmc", invalid)
    assert caught.value.diagnostic.code == "RKBC-002"


def test_any_and_all_match_only_the_declared_fields() -> None:
    connector = FakeConnector(
        [
            candidate(
                record_id="1",
                title="Targeted degradation platform",
                abstract="No route term.",
                doi="10.0000/synthetic.1",
            ),
            candidate(
                record_id="2",
                title="Delivery without the title phrase",
                abstract="Delivery is mentioned only here.",
                doi="10.0000/synthetic.2",
            ),
        ]
    )

    any_report = service(connector).search("europe-pmc", request())
    by_doi = {item["doi"]: item for item in any_report["results"]}
    assert set(by_doi) == {"10.0000/synthetic.1", "10.0000/synthetic.2"}
    assert by_doi["10.0000/synthetic.1"]["match_location"] == "title"
    assert by_doi["10.0000/synthetic.2"]["match_location"] == "abstract"

    all_report = service(connector).search(
        "europe-pmc",
        request(keyword_mode="all"),
    )
    assert all_report["results"] == []
    assert all_report["returned_result_count"] == 0


def test_local_date_preprint_and_keyword_filters_override_provider_over_return() -> None:
    connector = FakeConnector(
        [
            candidate(
                record_id="current",
                title="Targeted degradation study",
                abstract="Delivery was measured.",
                doi="10.0000/current",
            ),
            candidate(
                record_id="old",
                title="Targeted degradation study",
                abstract="Delivery was measured.",
                doi="10.0000/old",
                first_publication_date="2026-07-01",
            ),
            candidate(
                record_id="preprint",
                title="Targeted degradation preprint",
                abstract="Delivery was measured.",
                doi="10.0000/preprint",
                paper_type="preprint",
                source="PPR",
            ),
            candidate(
                record_id="mismatch",
                title="Unrelated mechanism",
                abstract="No requested term.",
                doi="10.0000/mismatch",
            ),
        ],
        hit_count=99,
    )
    report = service(connector).search(
        "europe-pmc",
        request(include_preprints=False),
    )

    assert [item["doi"] for item in report["results"]] == ["10.0000/current"]
    assert report["provider_hit_count"] == 99
    assert report["scanned_result_count"] == 4
    assert report["persistent_writes"] == 0


def test_zero_results_are_success_and_are_not_padded() -> None:
    report = service(FakeConnector([], hit_count=0)).search(
        "europe-pmc",
        request(max_results=15),
    )

    assert report["status"] == "success"
    assert report["returned_result_count"] == 0
    assert report["results"] == []
    assert report["truncated"] is False


def test_exact_doi_deduplicates_but_similar_titles_only_mark_candidates() -> None:
    sparse = candidate(
        record_id="doi-a",
        title="Targeted degradation delivery in a synthetic model",
        abstract="Delivery.",
        doi="https://doi.org/10.0000/DUPLICATE",
        journal_or_server=None,
    )
    rich = replace(
        sparse,
        abstract="Delivery and targeted degradation were both measured in the invented model.",
        journal_or_server="Invented Journal",
        discovery_sources=(DiscoverySource("europe-pmc", "MED", "doi-b"),),
    )
    similar = candidate(
        record_id="title-near",
        title="Targeted degradation delivery in the synthetic model",
        abstract="Delivery was independently described.",
        doi=None,
    )
    report = service(FakeConnector([sparse, rich, similar])).search(
        "europe-pmc",
        request(),
    )

    assert report["returned_result_count"] == 2
    doi_result = next(item for item in report["results"] if item["doi"] is not None)
    no_doi_result = next(item for item in report["results"] if item["doi"] is None)
    assert doi_result["doi"] == "10.0000/duplicate"
    assert len(doi_result["discovery_sources"]) == 2
    assert doi_result["journal_or_server"] == "Invented Journal"
    assert no_doi_result["result_key"] in doi_result["possible_duplicate_result_keys"]
    assert doi_result["result_key"] in no_doi_result["possible_duplicate_result_keys"]


def test_result_order_limit_and_truncation_are_deterministic() -> None:
    connector = FakeConnector(
        [
            candidate(
                record_id="b",
                title="Targeted degradation beta",
                abstract="Delivery.",
                doi="10.0000/b",
                first_publication_date="2026-07-19",
            ),
            candidate(
                record_id="a",
                title="Targeted degradation alpha",
                abstract="Delivery.",
                doi="10.0000/a",
                first_publication_date="2026-07-20",
            ),
            candidate(
                record_id="c",
                title="Targeted degradation gamma",
                abstract="Delivery.",
                doi="10.0000/c",
                first_publication_date="2026-07-18",
            ),
        ],
        exhausted=False,
    )
    discovery = service(connector)
    first = discovery.search("europe-pmc", request(max_results=2))
    second = discovery.search("europe-pmc", request(max_results=2))

    assert [item["doi"] for item in first["results"]] == [
        "10.0000/a",
        "10.0000/b",
    ]
    assert first["truncated"] is True
    assert serialize_json(first) == serialize_json(second)


def test_duplicate_or_unknown_connector_fails_closed() -> None:
    connector = FakeConnector()
    with pytest.raises(ResearchKBError) as duplicate:
        DiscoveryConnectorRegistry([connector, connector])
    assert duplicate.value.diagnostic.code == "RKBC-004"

    with pytest.raises(ResearchKBError) as unknown:
        service(connector).search("missing", request())
    assert unknown.value.diagnostic.code == "RKBC-032"


def test_invalid_provider_doi_fails_instead_of_becoming_identity() -> None:
    connector = FakeConnector(
        [
            candidate(
                record_id="invalid-doi",
                title="Targeted degradation study",
                abstract="Delivery was measured.",
                doi="not-a-doi/value",
            )
        ]
    )
    with pytest.raises(ResearchKBError) as caught:
        service(connector).search("europe-pmc", request())
    assert caught.value.diagnostic.code == "RKBC-033"


def test_malformed_provider_result_fails_with_bounded_diagnostic() -> None:
    class MalformedConnector(FakeConnector):
        def search(self, discovery_request):
            del discovery_request
            return DiscoveryProviderResult(
                provider="europe-pmc",
                provider_api_version="bad\nversion",
                provider_hit_count=0,
                scanned_result_count=0,
                exhausted=True,
                candidates=(),
            )

    with pytest.raises(ResearchKBError) as version:
        service(MalformedConnector()).search("europe-pmc", request())
    assert version.value.diagnostic.code == "RKBC-033"

    malformed = DiscoveryProviderResult(
        provider="europe-pmc",
        provider_api_version="synthetic-1",
        provider_hit_count=0,
        scanned_result_count=0,
        exhausted=True,
        candidates=(),
    )
    object.__setattr__(malformed, "candidates", None)

    class NoneCandidatesConnector(FakeConnector):
        def search(self, discovery_request):
            del discovery_request
            return malformed

    with pytest.raises(ResearchKBError) as candidates:
        service(NoneCandidatesConnector()).search("europe-pmc", request())
    assert candidates.value.diagnostic.code == "RKBC-033"
