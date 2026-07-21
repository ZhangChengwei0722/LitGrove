from __future__ import annotations

import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from research_kb import __version__
from research_kb.discovery.base import (
    DiscoveryCandidate,
    DiscoveryProviderResult,
    DiscoveryRequest,
    DiscoverySource,
)
from research_kb.errors import (
    DISCOVERY_CONNECTOR_ERROR,
    DISCOVERY_OUTPUT_INVALID,
    SCHEMA_VALIDATION_FAILED,
    Diagnostic,
    ResearchKBError,
)


EUROPE_PMC_ENDPOINT = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
EUROPE_PMC_QUERY_LIMIT = 1500
EUROPE_PMC_PAGE_SIZE = 100
EUROPE_PMC_MAX_PAGES = 10
EUROPE_PMC_MAX_RESULTS = 1000
EUROPE_PMC_TIMEOUT_SECONDS = 20
EUROPE_PMC_RESPONSE_LIMIT = 5 * 1024 * 1024


class JsonHttpTransport(Protocol):
    def get_json(
        self,
        *,
        endpoint: str,
        params: Mapping[str, object],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(newurl, code, "redirect rejected", headers, fp)


class UrlLibJsonTransport:
    def __init__(self, *, opener=None, response_limit: int = EUROPE_PMC_RESPONSE_LIMIT):
        self.opener = build_opener(_RejectRedirects()) if opener is None else opener
        self.response_limit = response_limit

    def get_json(
        self,
        *,
        endpoint: str,
        params: Mapping[str, object],
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        if endpoint != EUROPE_PMC_ENDPOINT:
            raise _connector_error("discovery connector attempted an unapproved endpoint")
        url = endpoint + "?" + urlencode(params)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": f"research-kb-core/{__version__}",
            },
            method="GET",
        )
        try:
            with self.opener.open(request, timeout=timeout_seconds) as response:
                if getattr(response, "status", None) != 200:
                    raise _connector_error("discovery connector returned an unsuccessful HTTP status")
                if not _same_endpoint(response.geturl(), endpoint):
                    raise _connector_error("discovery connector response changed endpoint")
                content_type = str(response.headers.get("Content-Type", "")).lower()
                if "json" not in content_type:
                    raise _connector_error("discovery connector returned a non-JSON response")
                content = response.read(self.response_limit + 1)
        except ResearchKBError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise _connector_error("discovery connector request failed") from error
        if len(content) > self.response_limit:
            raise _connector_error("discovery connector response exceeded the byte limit")
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise _output_error("discovery connector returned malformed JSON") from error
        if not isinstance(value, Mapping):
            raise _output_error("discovery connector JSON root must be an object")
        return value


class EuropePmcConnector:
    connector_id = "europe-pmc"
    network_required = True

    def __init__(self, *, transport: JsonHttpTransport | None = None):
        self.transport = UrlLibJsonTransport() if transport is None else transport

    def search(self, discovery_request: DiscoveryRequest) -> DiscoveryProviderResult:
        query = _build_query(discovery_request)
        cursor = "*"
        seen_cursors = {cursor}
        candidates: list[DiscoveryCandidate] = []
        scanned = 0
        hit_count: int | None = None
        api_version: str | None = None
        exhausted = False

        for _ in range(EUROPE_PMC_MAX_PAGES):
            payload = self.transport.get_json(
                endpoint=EUROPE_PMC_ENDPOINT,
                params={
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": EUROPE_PMC_PAGE_SIZE,
                    "cursorMark": cursor,
                },
                timeout_seconds=EUROPE_PMC_TIMEOUT_SECONDS,
            )
            page_version, page_hit_count, items, next_cursor = _parse_page(payload)
            if api_version is None:
                api_version = page_version
                hit_count = page_hit_count
            elif page_version != api_version or page_hit_count != hit_count:
                raise _output_error("Europe PMC paging metadata changed during one search")

            scanned += len(items)
            for item in items:
                normalized = _candidate(item)
                if normalized is not None:
                    candidates.append(normalized)

            assert hit_count is not None
            if not items and scanned < hit_count:
                raise _output_error("Europe PMC exhausted a page before its reported hit count")
            if scanned >= hit_count:
                exhausted = True
                break
            if next_cursor is None:
                break
            if next_cursor in seen_cursors:
                raise _output_error("Europe PMC returned a repeated cursor")
            if scanned >= EUROPE_PMC_MAX_RESULTS:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        if api_version is None or hit_count is None:
            raise _output_error("Europe PMC returned no page metadata")
        return DiscoveryProviderResult(
            provider=self.connector_id,
            provider_api_version=api_version,
            provider_hit_count=hit_count,
            scanned_result_count=scanned,
            exhausted=exhausted,
            candidates=tuple(candidates),
        )


def _build_query(request: DiscoveryRequest) -> str:
    terms: list[str] = []
    for keyword in request.title_keywords:
        terms.append(f'TITLE:"{_escape_phrase(keyword)}"')
    for keyword in request.abstract_keywords:
        terms.append(f'ABSTRACT:"{_escape_phrase(keyword)}"')
    operator = " OR " if request.keyword_mode == "any" else " AND "
    query = (
        f"FIRST_PDATE:[{request.date_from.isoformat()} TO {request.date_until.isoformat()}] "
        f"AND ({operator.join(terms)}) sort_date:y"
    )
    if len(query) > EUROPE_PMC_QUERY_LIMIT:
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                "discovery-request",
                None,
                "/title_keywords",
                "escaped Europe PMC query exceeds the provider length limit",
            )
        )
    return query


def _escape_phrase(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_page(payload: Mapping[str, Any]) -> tuple[str, int, list[Mapping[str, Any]], str | None]:
    if not isinstance(payload, Mapping):
        raise _output_error("Europe PMC page must be an object")
    version = payload.get("version")
    hit_count = payload.get("hitCount")
    result_list = payload.get("resultList")
    if not isinstance(version, str) or not 1 <= len(version) <= 64:
        raise _output_error("Europe PMC page has invalid version metadata")
    if isinstance(hit_count, bool) or not isinstance(hit_count, int) or hit_count < 0:
        raise _output_error("Europe PMC page has invalid hit count")
    if not isinstance(result_list, Mapping):
        raise _output_error("Europe PMC page has invalid result list")
    raw_items = result_list.get("result", [])
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list) or len(raw_items) > EUROPE_PMC_PAGE_SIZE:
        raise _output_error("Europe PMC page has an invalid result array")
    items: list[Mapping[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            raise _output_error("Europe PMC result must be an object")
        items.append(item)
    next_cursor = payload.get("nextCursorMark")
    if next_cursor is not None and (
        not isinstance(next_cursor, str) or not 1 <= len(next_cursor) <= 512
    ):
        raise _output_error("Europe PMC page has an invalid cursor")
    return version, hit_count, items, next_cursor


def _candidate(item: Mapping[str, Any]) -> DiscoveryCandidate | None:
    source = _required_text(item.get("source"), "source", 64)
    record_id = _required_text(item.get("id"), "id", 128)
    title = _optional_text(item.get("title"), "title", 2000)
    first_date = _optional_text(item.get("firstPublicationDate"), "firstPublicationDate", 10)
    if title is None or first_date is None:
        return None
    authors = _authors(item)
    abstract = _optional_text(item.get("abstractText"), "abstractText", 50000)
    journal_or_server = _journal_or_server(item, source)
    doi = _optional_text(item.get("doi"), "doi", 512)
    publication_types = _publication_types(item)
    paper_type = _paper_type(source, publication_types)
    return DiscoveryCandidate(
        title=_plain_text(title),
        authors=authors,
        first_publication_date=first_date,
        journal_or_server=journal_or_server,
        doi=doi,
        paper_type=paper_type,
        publication_types=publication_types,
        abstract=_plain_text(abstract) if abstract is not None else None,
        discovery_sources=(DiscoverySource("europe-pmc", source, record_id),),
        full_text_status=_full_text_status(item),
    )


def _authors(item: Mapping[str, Any]) -> tuple[str, ...]:
    author_list = item.get("authorList")
    if author_list is None:
        fallback = _optional_text(item.get("authorString"), "authorString", 10000)
        return () if fallback is None else (fallback.rstrip(". "),)
    if not isinstance(author_list, Mapping):
        raise _output_error("Europe PMC authorList must be an object")
    raw_authors = author_list.get("author", [])
    if not isinstance(raw_authors, list) or len(raw_authors) > 500:
        raise _output_error("Europe PMC author array is invalid")
    result: list[str] = []
    for raw in raw_authors:
        if not isinstance(raw, Mapping):
            raise _output_error("Europe PMC author must be an object")
        name = _optional_text(raw.get("fullName"), "fullName", 512)
        if name is not None:
            result.append(_plain_text(name))
    return tuple(result)


def _publication_types(item: Mapping[str, Any]) -> tuple[str, ...]:
    value = item.get("pubTypeList")
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise _output_error("Europe PMC pubTypeList must be an object")
    raw = value.get("pubType", [])
    if not isinstance(raw, list) or len(raw) > 100:
        raise _output_error("Europe PMC publication type array is invalid")
    return tuple(sorted({_required_text(item, "pubType", 256) for item in raw}))


def _journal_or_server(item: Mapping[str, Any], source: str) -> str | None:
    journal_info = item.get("journalInfo")
    if journal_info is not None and not isinstance(journal_info, Mapping):
        raise _output_error("Europe PMC journalInfo must be an object")
    if isinstance(journal_info, Mapping):
        journal = journal_info.get("journal")
        if journal is not None and not isinstance(journal, Mapping):
            raise _output_error("Europe PMC journal must be an object")
        if isinstance(journal, Mapping):
            title = _optional_text(journal.get("title"), "journal.title", 1000)
            if title is not None:
                return _plain_text(title)
    report = item.get("bookOrReportDetails")
    if report is not None and not isinstance(report, Mapping):
        raise _output_error("Europe PMC bookOrReportDetails must be an object")
    if isinstance(report, Mapping):
        publisher = _optional_text(report.get("publisher"), "publisher", 1000)
        if publisher is not None:
            return _plain_text(publisher)
    return source or None


def _paper_type(source: str, publication_types: tuple[str, ...]) -> str:
    values = {value.casefold() for value in publication_types}
    if source.upper() == "PPR" or "preprint" in values:
        return "preprint"
    if any(value in values for value in {"review", "systematic review", "meta-analysis"}):
        return "review"
    if any("article" in value for value in values):
        return "article"
    return "other"


def _full_text_status(item: Mapping[str, Any]) -> str:
    if item.get("isOpenAccess") == "Y":
        return "open_access"
    value = item.get("fullTextUrlList")
    if value is not None:
        if not isinstance(value, Mapping):
            raise _output_error("Europe PMC fullTextUrlList must be an object")
        links = value.get("fullTextUrl", [])
        if not isinstance(links, list) or len(links) > 100:
            raise _output_error("Europe PMC full-text link array is invalid")
        codes: set[str] = set()
        labels: set[str] = set()
        for link in links:
            if not isinstance(link, Mapping):
                raise _output_error("Europe PMC full-text link must be an object")
            code = _optional_text(link.get("availabilityCode"), "availabilityCode", 32)
            label = _optional_text(link.get("availability"), "availability", 128)
            if code is not None:
                codes.add(code.upper())
            if label is not None:
                labels.add(label.casefold())
        if "OA" in codes or any("open access" in value for value in labels):
            return "open_access"
        if "F" in codes or "free" in labels:
            return "free_to_read"
        if "S" in codes or any("subscription" in value for value in labels):
            return "subscription_required"
        if links:
            return "unknown"
    if item.get("hasPDF") == "N" and item.get("inPMC") == "N":
        return "unavailable"
    return "unknown"


class _PlainTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain_text(value: str) -> str:
    parser = _PlainTextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception as error:
        raise _output_error("Europe PMC text markup could not be normalized") from error
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _required_text(value: Any, field: str, maximum: int) -> str:
    result = _optional_text(value, field, maximum)
    if result is None:
        raise _output_error(f"Europe PMC {field} is required")
    return result


def _optional_text(value: Any, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise _output_error(f"Europe PMC {field} must be text")
    result = value.strip()
    if not result:
        return None
    if len(result) > maximum or any(ord(char) == 0 for char in result):
        raise _output_error(f"Europe PMC {field} exceeds its output boundary")
    return result


def _same_endpoint(value: str, endpoint: str) -> bool:
    actual = urlsplit(value)
    expected = urlsplit(endpoint)
    return (
        actual.scheme,
        actual.hostname,
        actual.port,
        actual.path,
    ) == (
        expected.scheme,
        expected.hostname,
        expected.port,
        expected.path,
    )


def _connector_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_CONNECTOR_ERROR, "discovery-connector", None, "", message)
    )


def _output_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_OUTPUT_INVALID, "discovery-provider-output", None, "", message)
    )


__all__ = ["EUROPE_PMC_ENDPOINT", "EuropePmcConnector", "UrlLibJsonTransport"]
