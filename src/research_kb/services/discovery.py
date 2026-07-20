from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from research_kb.discovery import (
    DiscoveryCandidate,
    DiscoveryConnector,
    DiscoveryProviderResult,
    DiscoveryRequest,
    DiscoverySource,
)
from research_kb.errors import (
    DISCOVERY_CONNECTOR_ERROR,
    DISCOVERY_OUTPUT_INVALID,
    DUPLICATE_ID,
    Diagnostic,
    ResearchKBError,
)


CONNECTOR_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SOURCE_VALUE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
DOI_VALUE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
PAPER_TYPES = {"preprint", "review", "article", "other"}
FULL_TEXT_STATUSES = {
    "open_access",
    "free_to_read",
    "subscription_required",
    "unavailable",
    "unknown",
}
POSSIBLE_DUPLICATE_RATIO = 0.92


class DiscoveryConnectorRegistry:
    def __init__(self, connectors: Iterable[DiscoveryConnector] = ()):
        self._connectors: dict[str, DiscoveryConnector] = {}
        for connector in connectors:
            connector_id = _connector_id(connector)
            if connector_id in self._connectors:
                raise ResearchKBError(
                    Diagnostic(
                        DUPLICATE_ID,
                        "discovery-connector",
                        connector_id,
                        "/connector_id",
                        "duplicate discovery connector ID",
                    )
                )
            self._connectors[connector_id] = connector

    def require(self, connector_id: str) -> DiscoveryConnector:
        connector = self._connectors.get(connector_id)
        if connector is None:
            raise _connector_error("discovery connector is not explicitly registered")
        return connector


class DiscoveryService:
    def __init__(self, registry: DiscoveryConnectorRegistry):
        self.registry = registry

    def search(self, connector_id: str, request_mapping: Mapping[str, Any]) -> dict[str, Any]:
        request = DiscoveryRequest.from_mapping(request_mapping)
        connector = self.registry.require(connector_id)
        try:
            provider_result = connector.search(request)
        except ResearchKBError:
            raise
        except Exception as error:
            raise _connector_error("discovery connector failed") from error
        _validate_provider_result(provider_result, connector_id)

        qualified: list[dict[str, Any]] = []
        for candidate in provider_result.candidates:
            normalized = _normalize_candidate(candidate, request, connector_id)
            if normalized is not None:
                qualified.append(normalized)
        deduplicated = _deduplicate_doi(qualified)
        deduplicated.sort(key=_result_sort_key)
        limited = deduplicated[: request.max_results]
        _mark_possible_duplicates(limited)
        return {
            "status": "success",
            "interface_version": "1.0",
            "provider": connector_id,
            "provider_api_version": provider_result.provider_api_version,
            "query": request.to_dict(),
            "provider_hit_count": provider_result.provider_hit_count,
            "scanned_result_count": provider_result.scanned_result_count,
            "returned_result_count": len(limited),
            "truncated": (not provider_result.exhausted) or len(deduplicated) > request.max_results,
            "persistent_writes": 0,
            "results": limited,
        }


def _connector_id(connector: DiscoveryConnector) -> str:
    try:
        connector_id = connector.connector_id
        network_required = connector.network_required
        search = connector.search
    except Exception as error:
        raise _connector_error("discovery connector metadata is incomplete") from error
    if not isinstance(connector_id, str) or not CONNECTOR_ID.fullmatch(connector_id):
        raise _connector_error("discovery connector ID is invalid")
    if not isinstance(network_required, bool) or not callable(search):
        raise _connector_error("discovery connector metadata is invalid")
    return connector_id


def _validate_provider_result(value: Any, connector_id: str) -> None:
    if not isinstance(value, DiscoveryProviderResult):
        raise _output_error("discovery connector returned an invalid result object")
    if value.provider != connector_id:
        raise _output_error("discovery connector result provider does not match its registration")
    if (
        not isinstance(value.provider_api_version, str)
        or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value.provider_api_version)
    ):
        raise _output_error("discovery provider version is invalid")
    if (
        isinstance(value.provider_hit_count, bool)
        or not isinstance(value.provider_hit_count, int)
        or value.provider_hit_count < 0
    ):
        raise _output_error("discovery provider hit count is invalid")
    if not isinstance(value.exhausted, bool) or not isinstance(value.candidates, tuple):
        raise _output_error("discovery provider completion state is invalid")
    if (
        isinstance(value.scanned_result_count, bool)
        or not isinstance(value.scanned_result_count, int)
        or value.scanned_result_count < len(value.candidates)
        or value.scanned_result_count > 1000
    ):
        raise _output_error("discovery provider scanned count is invalid")
    if value.provider_hit_count < value.scanned_result_count:
        raise _output_error("discovery provider hit count is smaller than scanned count")


def _normalize_candidate(
    candidate: DiscoveryCandidate,
    request: DiscoveryRequest,
    connector_id: str,
) -> dict[str, Any] | None:
    if not isinstance(candidate, DiscoveryCandidate):
        raise _output_error("discovery provider candidate is invalid")
    title = _text(candidate.title, "title", 2000, required=True)
    abstract = _text(candidate.abstract, "abstract", 50000, required=False)
    publication_date = _candidate_date(candidate.first_publication_date)
    if publication_date < request.date_from or publication_date > request.date_until:
        return None
    if candidate.paper_type not in PAPER_TYPES:
        raise _output_error("discovery provider paper type is invalid")
    if not request.include_preprints and candidate.paper_type == "preprint":
        return None
    if candidate.full_text_status not in FULL_TEXT_STATUSES:
        raise _output_error("discovery provider full-text status is invalid")
    matched_title = [keyword for keyword in request.title_keywords if _contains(title, keyword)]
    matched_abstract = [
        keyword for keyword in request.abstract_keywords if abstract is not None and _contains(abstract, keyword)
    ]
    if request.keyword_mode == "any":
        qualifies = bool(matched_title or matched_abstract)
    else:
        qualifies = (
            len(matched_title) == len(request.title_keywords)
            and len(matched_abstract) == len(request.abstract_keywords)
        )
    if not qualifies:
        return None

    sources = _sources(candidate.discovery_sources, connector_id)
    doi = _normalize_doi(candidate.doi)
    result_key = f"doi:{doi}" if doi is not None else _provider_result_key(sources[0])
    matched = _ordered_unique((*matched_title, *matched_abstract))
    location = "both" if matched_title and matched_abstract else "title" if matched_title else "abstract"
    authors = tuple(_text(value, "author", 512, required=True) for value in candidate.authors)
    if len(authors) > 500:
        raise _output_error("discovery provider author count exceeds its boundary")
    publication_types = tuple(
        sorted({_text(value, "publication_type", 256, required=True) for value in candidate.publication_types})
    )
    if len(publication_types) > 100:
        raise _output_error("discovery provider publication type count exceeds its boundary")
    journal = _text(candidate.journal_or_server, "journal_or_server", 1000, required=False)
    return {
        "result_key": result_key,
        "title": title,
        "authors": list(authors),
        "first_publication_date": publication_date.isoformat(),
        "journal_or_server": journal,
        "doi": doi,
        "paper_type": candidate.paper_type,
        "publication_types": list(publication_types),
        "abstract": abstract,
        "matched_keywords": matched,
        "match_location": location,
        "discovery_sources": [source.to_dict() for source in sources],
        "full_text_status": candidate.full_text_status,
        "version_relationship": {"status": "unresolved", "related_doi": None},
        "possible_duplicate_result_keys": [],
    }


def _sources(values: tuple[DiscoverySource, ...], connector_id: str) -> tuple[DiscoverySource, ...]:
    if not isinstance(values, tuple) or not values:
        raise _output_error("discovery candidate must have at least one source")
    result: dict[tuple[str, str, str], DiscoverySource] = {}
    for value in values:
        if not isinstance(value, DiscoverySource):
            raise _output_error("discovery candidate source is invalid")
        if value.provider != connector_id:
            raise _output_error("discovery candidate source provider is invalid")
        if not SOURCE_VALUE.fullmatch(value.source) or not SOURCE_VALUE.fullmatch(value.record_id):
            raise _output_error("discovery candidate source identity is invalid")
        result[(value.provider, value.source, value.record_id)] = value
    return tuple(result[key] for key in sorted(result))


def _normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    doi = DOI_PREFIX.sub("", _text(value, "doi", 512, required=True)).strip().casefold()
    if not DOI_VALUE.fullmatch(doi) or any(char.isspace() for char in doi):
        raise _output_error("discovery provider DOI is invalid")
    return doi


def _deduplicate_doi(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        key = value["result_key"]
        existing = result.get(key)
        if existing is None or value["doi"] is None:
            if existing is None:
                result[key] = value
            else:
                unique_key = key
                index = 2
                while unique_key in result:
                    unique_key = f"{key}:{index}"
                    index += 1
                value["result_key"] = unique_key
                result[unique_key] = value
            continue
        preferred, _ = _preferred(existing, value)
        preferred["discovery_sources"] = _merge_dicts(
            existing["discovery_sources"],
            value["discovery_sources"],
            keys=("provider", "source", "record_id"),
        )
        preferred["matched_keywords"] = _ordered_unique(
            (*existing["matched_keywords"], *value["matched_keywords"])
        )
        locations = {existing["match_location"], value["match_location"]}
        preferred["match_location"] = "both" if "both" in locations or locations == {"title", "abstract"} else next(iter(locations))
        result[key] = preferred
    return list(result.values())


def _preferred(left: dict[str, Any], right: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    def score(value: dict[str, Any]) -> tuple[int, int, int, int, str]:
        return (
            sum(value[field] is not None for field in ("abstract", "journal_or_server")),
            len(value["abstract"] or ""),
            len(value["authors"]),
            len(value["publication_types"]),
            value["discovery_sources"][0]["record_id"],
        )

    return (right, left) if score(right) > score(left) else (left, right)


def _mark_possible_duplicates(values: list[dict[str, Any]]) -> None:
    for index, left in enumerate(values):
        for right in values[index + 1 :]:
            ratio = SequenceMatcher(
                None,
                _title_identity(left["title"]),
                _title_identity(right["title"]),
                autojunk=False,
            ).ratio()
            if ratio >= POSSIBLE_DUPLICATE_RATIO:
                left["possible_duplicate_result_keys"].append(right["result_key"])
                right["possible_duplicate_result_keys"].append(left["result_key"])
    for value in values:
        value["possible_duplicate_result_keys"].sort()


def _result_sort_key(value: dict[str, Any]) -> tuple[int, str, str]:
    ordinal = date.fromisoformat(value["first_publication_date"]).toordinal()
    return (-ordinal, _title_identity(value["title"]), value["result_key"])


def _contains(haystack: str, needle: str) -> bool:
    return _search_text(needle) in _search_text(haystack)


def _search_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def _title_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w]+", " ", normalized).strip()


def _ordered_unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        identity = value.casefold()
        if identity not in seen:
            seen.add(identity)
            result.append(value)
    return result


def _merge_dicts(left, right, *, keys: tuple[str, ...]):
    values = {tuple(item[key] for key in keys): item for item in (*left, *right)}
    return [values[key] for key in sorted(values)]


def _provider_result_key(source: DiscoverySource) -> str:
    return f"{source.provider}:{source.source.casefold()}:{source.record_id}"


def _candidate_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise _output_error("discovery provider publication date is invalid")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _output_error("discovery provider publication date is invalid") from error


def _text(value: Any, field: str, maximum: int, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise _output_error(f"discovery provider {field} is required")
        return None
    if not isinstance(value, str):
        raise _output_error(f"discovery provider {field} must be text")
    result = value.strip()
    if not result:
        if required:
            raise _output_error(f"discovery provider {field} is required")
        return None
    if len(result) > maximum or any(ord(char) == 0 for char in result):
        raise _output_error(f"discovery provider {field} exceeds its boundary")
    return result


def _connector_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_CONNECTOR_ERROR, "discovery-connector", None, "", message)
    )


def _output_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(DISCOVERY_OUTPUT_INVALID, "discovery-provider-output", None, "", message)
    )


__all__ = ["DiscoveryConnectorRegistry", "DiscoveryService"]
