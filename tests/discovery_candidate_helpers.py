from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any


def discovery_result(
    *,
    result_key: str = "doi:10.0000/synthetic.discovery",
    title: str = "Targeted degradation delivery in an invented system",
    doi: str | None = "10.0000/synthetic.discovery",
    record_id: str = "SYNTH-DISCOVERY-1",
) -> dict[str, Any]:
    return {
        "result_key": result_key,
        "title": title,
        "authors": ["Alpha Researcher"],
        "first_publication_date": "2026-07-20",
        "journal_or_server": "Invented Journal",
        "doi": doi,
        "paper_type": "article",
        "publication_types": ["Journal Article"],
        "abstract": "Delivery was measured in the fabricated study.",
        "matched_keywords": ["targeted degradation", "delivery"],
        "match_location": "both",
        "discovery_sources": [
            {
                "provider": "europe-pmc",
                "source": "MED",
                "record_id": record_id,
            }
        ],
        "full_text_status": "unknown",
        "version_relationship": {"status": "unresolved", "related_doi": None},
        "possible_duplicate_result_keys": [],
    }


def discovery_report(*results: dict[str, Any], **query_overrides: Any) -> dict[str, Any]:
    query = {
        "date_from": "2026-07-14",
        "date_until": "2026-07-21",
        "title_keywords": ["targeted degradation"],
        "abstract_keywords": ["delivery"],
        "keyword_mode": "any",
        "include_preprints": True,
        "max_results": 15,
    }
    query.update(query_overrides)
    values = [deepcopy(item) for item in (results or (discovery_result(),))]
    values.sort(
        key=lambda item: (
            -date.fromisoformat(item["first_publication_date"]).toordinal(),
            item["title"].casefold(),
            item["result_key"],
        )
    )
    return {
        "status": "success",
        "interface_version": "1.0",
        "provider": "europe-pmc",
        "provider_api_version": "synthetic-6.9",
        "query": query,
        "provider_hit_count": len(values),
        "scanned_result_count": len(values),
        "returned_result_count": len(values),
        "truncated": False,
        "persistent_writes": 0,
        "results": values,
    }


def selection_request(
    report: dict[str, Any] | None = None,
    *,
    result_keys: list[str] | None = None,
    target_question_ids: list[str] | None = None,
) -> dict[str, Any]:
    report = deepcopy(report or discovery_report())
    keys = result_keys or [report["results"][0]["result_key"]]
    return {
        "request_version": "1.0",
        "report": report,
        "selections": [
            {
                "result_key": key,
                "target_question_ids": list(target_question_ids or []),
            }
            for key in keys
        ],
        "fixture_origin": "synthetic_from_scratch",
    }
