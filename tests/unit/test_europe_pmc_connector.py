from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from research_kb.discovery.base import DiscoveryRequest
from research_kb.discovery.europe_pmc import (
    EUROPE_PMC_ENDPOINT,
    EuropePmcConnector,
    UrlLibJsonTransport,
)
from research_kb.errors import Diagnostic, ResearchKBError


def discovery_request(**overrides) -> DiscoveryRequest:
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
    return DiscoveryRequest.from_mapping(value)


class FakeTransport:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_json(self, *, endpoint, params, timeout_seconds):
        self.calls.append((endpoint, dict(params), timeout_seconds))
        return self.pages[params["cursorMark"]]


def provider_item(**overrides):
    value = {
        "id": "SYNTH-1",
        "source": "MED",
        "doi": "10.0000/SYNTHETIC",
        "title": "Targeted degradation delivery in an invented system",
        "authorList": {"author": [{"fullName": "Alpha Researcher"}]},
        "journalInfo": {"journal": {"title": "Invented Journal"}},
        "abstractText": "<h4>Background</h4><p>Delivery was measured.</p>",
        "pubTypeList": {"pubType": ["Journal Article"]},
        "fullTextUrlList": {
            "fullTextUrl": [
                {"availability": "Subscription required", "availabilityCode": "S"}
            ]
        },
        "firstPublicationDate": "2026-07-20",
    }
    value.update(overrides)
    return value


def page(*items, cursor=None, hit_count=None):
    return {
        "version": "synthetic-6.9",
        "hitCount": len(items) if hit_count is None else hit_count,
        "nextCursorMark": cursor,
        "nextPageUrl": "https://untrusted.invalid/must-not-be-used",
        "resultList": {"result": list(items)},
    }


def test_connector_uses_fixed_endpoint_cursor_and_core_result_type() -> None:
    transport = FakeTransport(
        {
            "*": page(provider_item(), cursor="next", hit_count=2),
            "next": page(
                provider_item(
                    id="SYNTH-2",
                    source="PPR",
                    doi="10.0000/PREPRINT",
                    pubTypeList={"pubType": ["Preprint"]},
                    journalInfo=None,
                    bookOrReportDetails={"publisher": "Invented Preprint Server"},
                    fullTextUrlList={
                        "fullTextUrl": [{"availability": "Free", "availabilityCode": "F"}]
                    },
                ),
                hit_count=2,
            ),
        }
    )
    result = EuropePmcConnector(transport=transport).search(discovery_request())

    assert [call[0] for call in transport.calls] == [EUROPE_PMC_ENDPOINT, EUROPE_PMC_ENDPOINT]
    assert transport.calls[0][1]["resultType"] == "core"
    assert transport.calls[0][1]["format"] == "json"
    assert transport.calls[0][1]["cursorMark"] == "*"
    assert transport.calls[1][1]["cursorMark"] == "next"
    assert "FIRST_PDATE:[2026-07-14 TO 2026-07-21]" in transport.calls[0][1]["query"]
    assert 'TITLE:"targeted degradation"' in transport.calls[0][1]["query"]
    assert 'ABSTRACT:"delivery"' in transport.calls[0][1]["query"]
    assert "sort_date:y" in transport.calls[0][1]["query"]
    assert result.provider_hit_count == 2
    assert result.scanned_result_count == 2
    assert result.exhausted is True
    assert result.candidates[0].abstract == "Background Delivery was measured."
    assert result.candidates[0].full_text_status == "subscription_required"
    assert result.candidates[1].paper_type == "preprint"
    assert result.candidates[1].journal_or_server == "Invented Preprint Server"
    assert result.candidates[1].full_text_status == "free_to_read"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": "1", "hitCount": -1, "resultList": {"result": []}},
        {"version": "1", "hitCount": 1, "resultList": {"result": ["bad"]}},
        {"version": "1", "hitCount": 1, "resultList": {"result": [{}]}},
        {"version": "1", "hitCount": 1, "resultList": {"result": [provider_item(journalInfo="bad")]}},
        {"version": "1", "hitCount": 1, "nextCursorMark": "*", "resultList": {"result": []}},
    ],
)
def test_invalid_provider_shape_fails_closed(payload) -> None:
    transport = FakeTransport({"*": payload})
    with pytest.raises(ResearchKBError) as caught:
        EuropePmcConnector(transport=transport).search(discovery_request())
    assert caught.value.diagnostic.code == "RKBC-033"


def test_transport_or_later_page_failure_has_no_partial_provider_result() -> None:
    class FailingTransport(FakeTransport):
        def get_json(self, *, endpoint, params, timeout_seconds):
            if params["cursorMark"] == "next":
                raise ResearchKBError(
                    Diagnostic("RKBC-032", "discovery-connector", None, "", "synthetic failure")
                )
            return super().get_json(
                endpoint=endpoint,
                params=params,
                timeout_seconds=timeout_seconds,
            )

    transport = FailingTransport({"*": page(provider_item(), cursor="next", hit_count=2)})
    with pytest.raises(ResearchKBError) as caught:
        EuropePmcConnector(transport=transport).search(discovery_request())
    assert caught.value.diagnostic.code == "RKBC-032"


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_type="application/json",
        final_url=EUROPE_PMC_ENDPOINT,
        status=200,
    ):
        self._body = BytesIO(body)
        self.headers = {"Content-Type": content_type}
        self.status = status
        self._final_url = final_url

    def read(self, size=-1):
        return self._body.read(size)

    def geturl(self):
        return self._final_url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeOpener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def test_stdlib_transport_bounds_json_and_rejects_wrong_destination() -> None:
    payload = json.dumps({"value": "synthetic"}).encode("utf-8")
    opener = FakeOpener(FakeResponse(payload))
    transport = UrlLibJsonTransport(opener=opener, response_limit=1024)
    assert transport.get_json(
        endpoint=EUROPE_PMC_ENDPOINT,
        params={"query": "synthetic"},
        timeout_seconds=20,
    ) == {"value": "synthetic"}
    assert opener.requests[0][0].full_url.startswith(EUROPE_PMC_ENDPOINT + "?")

    oversized = UrlLibJsonTransport(
        opener=FakeOpener(FakeResponse(b"{" + b"x" * 20 + b"}")),
        response_limit=10,
    )
    with pytest.raises(ResearchKBError) as too_large:
        oversized.get_json(
            endpoint=EUROPE_PMC_ENDPOINT,
            params={"query": "synthetic"},
            timeout_seconds=20,
        )
    assert too_large.value.diagnostic.code == "RKBC-032"

    redirected = UrlLibJsonTransport(
        opener=FakeOpener(FakeResponse(payload, final_url="https://untrusted.invalid/result")),
    )
    with pytest.raises(ResearchKBError) as redirect:
        redirected.get_json(
            endpoint=EUROPE_PMC_ENDPOINT,
            params={"query": "synthetic"},
            timeout_seconds=20,
        )
    assert redirect.value.diagnostic.code == "RKBC-032"

    unsuccessful = UrlLibJsonTransport(
        opener=FakeOpener(FakeResponse(payload, status=500)),
    )
    with pytest.raises(ResearchKBError) as status:
        unsuccessful.get_json(
            endpoint=EUROPE_PMC_ENDPOINT,
            params={"query": "synthetic"},
            timeout_seconds=20,
        )
    assert status.value.diagnostic.code == "RKBC-032"


def test_stdlib_transport_maps_http_failure_without_echoing_payload() -> None:
    error = HTTPError(EUROPE_PMC_ENDPOINT, 503, "unavailable", {}, None)
    transport = UrlLibJsonTransport(opener=FakeOpener(error=error))
    with pytest.raises(ResearchKBError) as caught:
        transport.get_json(
            endpoint=EUROPE_PMC_ENDPOINT,
            params={"query": "private-looking keyword"},
            timeout_seconds=20,
        )
    assert caught.value.diagnostic.code == "RKBC-032"
    assert "private-looking keyword" not in caught.value.diagnostic.message
