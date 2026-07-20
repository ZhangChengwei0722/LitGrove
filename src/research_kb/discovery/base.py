from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, runtime_checkable

from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError


REQUEST_FIELDS = {
    "request_version",
    "date_from",
    "date_until",
    "title_keywords",
    "abstract_keywords",
    "keyword_mode",
    "include_preprints",
    "max_results",
}
MAX_KEYWORDS_PER_FIELD = 20
MAX_KEYWORD_LENGTH = 128
MAX_DATE_SPAN_DAYS = 31
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    request_version: str
    date_from: date
    date_until: date
    title_keywords: tuple[str, ...]
    abstract_keywords: tuple[str, ...]
    keyword_mode: str
    include_preprints: bool
    max_results: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "DiscoveryRequest":
        if not isinstance(value, Mapping):
            raise _request_error("discovery request must be a JSON object", "")
        if set(value) != REQUEST_FIELDS:
            raise _request_error("discovery request fields do not match the interface contract", "")
        if value["request_version"] != "1.0":
            raise _request_error("unsupported discovery request version", "/request_version")
        date_from = _parse_date(value["date_from"], "/date_from")
        date_until = _parse_date(value["date_until"], "/date_until")
        if date_from > date_until:
            raise _request_error("date_from must not be later than date_until", "/date_from")
        if (date_until - date_from).days > MAX_DATE_SPAN_DAYS:
            raise _request_error("discovery date range exceeds 31 days", "/date_until")
        title_keywords = _keywords(value["title_keywords"], "/title_keywords")
        abstract_keywords = _keywords(value["abstract_keywords"], "/abstract_keywords")
        if not title_keywords and not abstract_keywords:
            raise _request_error("at least one title or abstract keyword is required", "/title_keywords")
        keyword_mode = value["keyword_mode"]
        if keyword_mode not in {"any", "all"}:
            raise _request_error("keyword_mode must be any or all", "/keyword_mode")
        include_preprints = value["include_preprints"]
        if not isinstance(include_preprints, bool):
            raise _request_error("include_preprints must be a boolean", "/include_preprints")
        max_results = value["max_results"]
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 15:
            raise _request_error("max_results must be an integer from 1 through 15", "/max_results")
        return cls(
            request_version="1.0",
            date_from=date_from,
            date_until=date_until,
            title_keywords=title_keywords,
            abstract_keywords=abstract_keywords,
            keyword_mode=keyword_mode,
            include_preprints=include_preprints,
            max_results=max_results,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(),
            "date_until": self.date_until.isoformat(),
            "title_keywords": list(self.title_keywords),
            "abstract_keywords": list(self.abstract_keywords),
            "keyword_mode": self.keyword_mode,
            "include_preprints": self.include_preprints,
            "max_results": self.max_results,
        }


@dataclass(frozen=True, slots=True)
class DiscoverySource:
    provider: str
    source: str
    record_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider,
            "source": self.source,
            "record_id": self.record_id,
        }


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    title: str
    authors: tuple[str, ...]
    first_publication_date: str
    journal_or_server: str | None
    doi: str | None
    paper_type: str
    publication_types: tuple[str, ...]
    abstract: str | None
    discovery_sources: tuple[DiscoverySource, ...]
    full_text_status: str


@dataclass(frozen=True, slots=True)
class DiscoveryProviderResult:
    provider: str
    provider_api_version: str
    provider_hit_count: int
    scanned_result_count: int
    exhausted: bool
    candidates: tuple[DiscoveryCandidate, ...]


@runtime_checkable
class DiscoveryConnector(Protocol):
    connector_id: str
    network_required: bool

    def search(self, discovery_request: DiscoveryRequest) -> DiscoveryProviderResult:
        ...


def _parse_date(value: Any, path: str) -> date:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise _request_error("date must use YYYY-MM-DD", path)
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise _request_error("date is not a valid calendar date", path) from error


def _keywords(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_KEYWORDS_PER_FIELD:
        raise _request_error("keyword field must be an array with at most 20 items", path)
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}/{index}"
        if not isinstance(item, str):
            raise _request_error("keyword must be a string", item_path)
        normalized = item.strip()
        if not normalized or len(normalized) > MAX_KEYWORD_LENGTH:
            raise _request_error("keyword length is outside the allowed range", item_path)
        if any(unicodedata.category(char).startswith("C") for char in normalized):
            raise _request_error("keyword contains a control character", item_path)
        identity = normalized.casefold()
        if identity in seen:
            raise _request_error("keyword is duplicated within its field", item_path)
        seen.add(identity)
        result.append(normalized)
    return tuple(result)


def _request_error(message: str, path: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(SCHEMA_VALIDATION_FAILED, "discovery-request", None, path, message)
    )


__all__ = [
    "DiscoveryCandidate",
    "DiscoveryConnector",
    "DiscoveryProviderResult",
    "DiscoveryRequest",
    "DiscoverySource",
]
