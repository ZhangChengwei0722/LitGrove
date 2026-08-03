from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from research_kb.catalog import CatalogDatabase, CatalogDocument, CatalogSnapshot, CatalogSourceRecord
from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError


WORKSPACE_ID = "workspace_a1111111-1111-4111-8111-111111111111"
PAPER_A = "paper_a1111111-1111-4111-8111-111111111111"
PAPER_B = "paper_b2222222-2222-4222-8222-222222222222"
QUESTION_A = "question_a1111111-1111-4111-8111-111111111111"
QUESTION_B = "question_b2222222-2222-4222-8222-222222222222"
TAG_A = "tag_a1111111-1111-4111-8111-111111111111"
TAG_B = "tag_b2222222-2222-4222-8222-222222222222"


def _snapshot(
    sources: list[tuple[str, str]],
    *,
    workspace_id: str = WORKSPACE_ID,
    registry_version: str = "1.0",
) -> CatalogSnapshot:
    source_records: list[CatalogSourceRecord] = []
    documents: list[CatalogDocument] = []
    for ordinal, (record_id, title) in enumerate(sources):
        source_key = f"registry-paper:{record_id}"
        digest = canonical_digest({"record_id": record_id, "title": title})
        source_records.append(
            CatalogSourceRecord(source_key, "registry-paper", record_id, digest, "1.0")
        )
        documents.append(
            CatalogDocument(
                item_id="catalog_" + canonical_digest(source_key)[:32],
                item_kind="paper",
                authority_layer="canonical",
                source_key=source_key,
                record_kind="registry-paper",
                record_id=record_id,
                child_id=None,
                paper_id=record_id,
                question_id=None,
                title=title,
                summary=f"Synthetic summary {ordinal}",
                status_labels=("review:ai_checked",),
                search_text=f"{title} fabricated-token-{ordinal}",
                sort_key=title.casefold(),
                source_record_digest=digest,
                adapter_version="1.0",
            )
        )
    source_records.sort(key=lambda item: item.source_key)
    documents.sort(key=lambda item: (item.sort_key, item.item_kind, item.item_id))
    watermark = canonical_digest(
        {
            "registry_version": registry_version,
            "sources": [
                [item.source_key, item.source_record_digest, item.adapter_version]
                for item in source_records
            ],
        }
    )
    return CatalogSnapshot(
        workspace_id,
        registry_version,
        watermark,
        tuple(source_records),
        tuple(documents),
        (),
    )


def _all_items(path: Path) -> list[dict]:
    return CatalogDatabase.query(path, page_size=100)["items"]


def _filter_snapshot() -> CatalogSnapshot:
    source_records: list[CatalogSourceRecord] = []
    documents: list[CatalogDocument] = []
    values = (
        ("paper-a", "Alpha one", PAPER_A, QUESTION_A),
        ("paper-b", "Alpha two", PAPER_A, QUESTION_B),
        ("paper-c", "Beta one", PAPER_B, QUESTION_A),
        ("paper-d", "Beta two", PAPER_B, QUESTION_B),
    )
    for record_id, title, paper_id, question_id in values:
        source_key = f"registry-paper:{record_id}"
        digest = canonical_digest({"record_id": record_id, "title": title})
        source_records.append(
            CatalogSourceRecord(source_key, "registry-paper", record_id, digest, "1.0")
        )
        documents.append(
            CatalogDocument(
                item_id="catalog_" + canonical_digest(source_key)[:32],
                item_kind="paper",
                authority_layer="canonical",
                source_key=source_key,
                record_kind="registry-paper",
                record_id=record_id,
                child_id=None,
                paper_id=paper_id,
                question_id=question_id,
                title=title,
                summary="Synthetic filter summary",
                status_labels=("review:ai_checked",),
                search_text=f"{title} fabricated-filter",
                sort_key=title.casefold(),
                source_record_digest=digest,
                adapter_version="1.0",
            )
        )
    source_records.sort(key=lambda item: item.source_key)
    documents.sort(key=lambda item: (item.sort_key, item.item_kind, item.item_id))
    watermark = canonical_digest(
        {
            "registry_version": "1.0",
            "sources": [
                [item.source_key, item.source_record_digest, item.adapter_version]
                for item in source_records
            ],
        }
    )
    return CatalogSnapshot(
        WORKSPACE_ID,
        "1.0",
        watermark,
        tuple(source_records),
        tuple(documents),
        (),
    )


def _tagged_filter_snapshot() -> CatalogSnapshot:
    snapshot = _filter_snapshot()
    documents = tuple(
        replace(
            document,
            tag_ids=(TAG_A, TAG_B) if document.record_id == "paper-a" else (TAG_A,),
            tag_names=("Alpha tag", "Beta tag") if document.record_id == "paper-a" else ("Alpha tag",),
        )
        for document in snapshot.documents
    )
    return replace(snapshot, documents=documents)


def test_incremental_add_change_remove_matches_full_rebuild_without_fts_orphans(
    tmp_path: Path,
) -> None:
    incremental = tmp_path / "incremental.sqlite3"
    rebuilt = tmp_path / "rebuilt.sqlite3"
    first = _snapshot([("paper-a", "Alpha old"), ("paper-b", "Beta unchanged")])
    second = _snapshot([("paper-a", "Alpha revised"), ("paper-c", "Gamma added")])
    CatalogDatabase.build(incremental, first, build_mode="full")

    result = CatalogDatabase.update(incremental, second)
    CatalogDatabase.build(rebuilt, second, build_mode="full")

    assert result == {
        "changed_source_count": 3,
        "removed_source_count": 1,
        "item_count": 2,
    }
    assert _all_items(incremental) == _all_items(rebuilt)
    assert CatalogDatabase.query(incremental, query="old")["items"] == []
    assert [item["title"] for item in CatalogDatabase.query(incremental, query="revised")["items"]] == [
        "Alpha revised"
    ]
    assert all(item["tags"] == [] for item in _all_items(incremental))
    assert CatalogDatabase.detail_row(incremental, _all_items(incremental)[0]["item_id"])["tags"] == []
    with sqlite3.connect(incremental) as connection:
        assert connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM catalog_fts").fetchone()[0] == 2


def test_incremental_update_preserves_unchanged_rows(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    first = _snapshot([("paper-a", "Alpha"), ("paper-b", "Beta")])
    second = _snapshot([("paper-a", "Alpha revised"), ("paper-b", "Beta")])
    CatalogDatabase.build(path, first, build_mode="full")
    with sqlite3.connect(path) as connection:
        before = connection.execute(
            "SELECT rowid FROM catalog_items WHERE record_id = 'paper-b'"
        ).fetchone()[0]

    CatalogDatabase.update(path, second)

    with sqlite3.connect(path) as connection:
        after = connection.execute(
            "SELECT rowid FROM catalog_items WHERE record_id = 'paper-b'"
        ).fetchone()[0]
    assert after == before
    assert {item["title"] for item in _all_items(path)} == {"Alpha revised", "Beta"}


def test_registry_version_change_reprojects_every_source(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    first = _snapshot([("paper-a", "Alpha"), ("paper-b", "Beta")])
    second = _snapshot(
        [("paper-a", "Alpha"), ("paper-b", "Beta")],
        registry_version="2.0",
    )
    CatalogDatabase.build(path, first, build_mode="full")

    result = CatalogDatabase.update(path, second)

    assert result["changed_source_count"] == 2
    assert CatalogDatabase.inspect(path).metadata["adapter_registry_version"] == "2.0"


def test_incremental_update_rejects_workspace_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(path, _snapshot([("paper-a", "Alpha")]), build_mode="full")
    mismatched = _snapshot(
        [("paper-a", "Alpha")],
        workspace_id="workspace_b2222222-2222-4222-8222-222222222222",
    )

    with pytest.raises(ResearchKBError) as caught:
        CatalogDatabase.update(path, mismatched)

    assert caught.value.diagnostic.code == "RKBC-036"
    assert [item["title"] for item in _all_items(path)] == ["Alpha"]


def test_inspection_classifies_missing_corrupt_and_incompatible(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    assert CatalogDatabase.inspect(path).state == "missing"
    path.write_bytes(b"not-sqlite")
    assert CatalogDatabase.inspect(path).state == "corrupt"
    path.unlink()
    CatalogDatabase.build(path, _snapshot([("paper-a", "Alpha")]), build_mode="full")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE catalog_metadata SET value = '99.0' WHERE key = 'catalog_contract_version'"
        )
    assert CatalogDatabase.inspect(path).state == "incompatible"


def test_inspection_detects_missing_or_tampered_tag_facets(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(path, _tagged_filter_snapshot(), build_mode="full")
    inspection = CatalogDatabase.inspect(path)
    assert inspection.state == "ready"
    assert inspection.metadata["facet_count"] == 5
    assert len(inspection.metadata["facet_digest"]) == 64

    with sqlite3.connect(path) as connection:
        connection.execute(
            "DELETE FROM catalog_item_tags WHERE item_id = (SELECT item_id FROM catalog_items WHERE record_id = 'paper-a') AND tag_id = ?",
            (TAG_B,),
        )

    assert CatalogDatabase.inspect(path).state == "corrupt"


def test_cursor_pagination_is_complete_and_bound_to_query(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    snapshot = _snapshot(
        [
            ("paper-c", "Same title"),
            ("paper-a", "Alpha"),
            ("paper-b", "Same title"),
            ("paper-d", "Zulu"),
        ]
    )
    CatalogDatabase.build(path, snapshot, build_mode="full")
    seen: list[str] = []
    cursor = None
    while True:
        page = CatalogDatabase.query(path, page_size=1, cursor=cursor)
        seen.extend(item["item_id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert len(seen) == len(set(seen)) == 4
    assert seen == [item["item_id"] for item in _all_items(path)]
    first_page = CatalogDatabase.query(path, page_size=1)
    with pytest.raises(ResearchKBError) as mismatch:
        CatalogDatabase.query(path, query="Alpha", page_size=1, cursor=first_page["next_cursor"])
    assert mismatch.value.diagnostic.code == "RKBC-037"


def test_cursor_rejects_missing_position_malformed_value_and_oversized_page(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(
        path,
        _snapshot([("paper-a", "Alpha"), ("paper-b", "Beta")]),
        build_mode="full",
    )
    first_page = CatalogDatabase.query(path, page_size=1)
    decoded = json.loads(
        base64.urlsafe_b64decode(first_page["next_cursor"] + "==").decode("utf-8")
    )
    decoded["last"] = ["missing", "paper", "catalog_" + "0" * 32]
    tampered = base64.urlsafe_b64encode(
        json.dumps(decoded, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")

    with pytest.raises(ResearchKBError) as missing:
        CatalogDatabase.query(path, page_size=1, cursor=tampered)
    with pytest.raises(ResearchKBError) as malformed:
        CatalogDatabase.query(path, cursor="a")
    with pytest.raises(ResearchKBError) as oversized:
        CatalogDatabase.query(path, page_size=101)

    assert {error.value.diagnostic.code for error in (missing, malformed, oversized)} == {
        "RKBC-037"
    }


def test_exact_paper_and_question_filters_combine_with_search_and_pagination(
    tmp_path: Path,
) -> None:
    path = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(path, _filter_snapshot(), build_mode="full")

    paper_page = CatalogDatabase.query(path, paper_id=PAPER_A, page_size=1)
    combined = CatalogDatabase.query(
        path,
        query="Alpha",
        paper_id=PAPER_A,
        question_id=QUESTION_B,
    )
    no_match = CatalogDatabase.query(path, paper_id=PAPER_A, question_id=QUESTION_A, query="Beta")

    assert paper_page["paper_id"] == PAPER_A
    assert paper_page["items"][0]["paper_id"] == PAPER_A
    assert paper_page["has_more"] is True
    second_page = CatalogDatabase.query(
        path,
        paper_id=PAPER_A,
        page_size=1,
        cursor=paper_page["next_cursor"],
    )
    assert [item["title"] for item in paper_page["items"] + second_page["items"]] == [
        "Alpha one",
        "Alpha two",
    ]
    assert combined["paper_id"] == PAPER_A
    assert combined["question_id"] == QUESTION_B
    assert [item["title"] for item in combined["items"]] == ["Alpha two"]
    assert no_match["items"] == []
    assert "paper_id" not in CatalogDatabase.query(path)
    assert "question_id" not in CatalogDatabase.query(path)


def test_filter_ids_and_cursor_query_identity_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(path, _filter_snapshot(), build_mode="full")
    first_page = CatalogDatabase.query(path, paper_id=PAPER_A, page_size=1)

    with pytest.raises(ResearchKBError) as paper_cursor_mismatch:
        CatalogDatabase.query(
            path,
            paper_id=PAPER_B,
            page_size=1,
            cursor=first_page["next_cursor"],
        )
    with pytest.raises(ResearchKBError) as question_cursor_mismatch:
        CatalogDatabase.query(
            path,
            paper_id=PAPER_A,
            question_id=QUESTION_A,
            page_size=1,
            cursor=first_page["next_cursor"],
        )
    with pytest.raises(ResearchKBError) as malformed_paper:
        CatalogDatabase.query(path, paper_id="paper-not-valid")
    with pytest.raises(ResearchKBError) as malformed_question:
        CatalogDatabase.query(path, question_id="question-not-valid")

    assert paper_cursor_mismatch.value.diagnostic.code == "RKBC-037"
    assert question_cursor_mismatch.value.diagnostic.code == "RKBC-037"
    assert malformed_paper.value.diagnostic.code == "RKBC-002"
    assert malformed_question.value.diagnostic.code == "RKBC-002"


def test_tag_filter_cursor_pagination_is_complete_and_filter_bound(tmp_path: Path) -> None:
    path = tmp_path / "catalog.sqlite3"
    CatalogDatabase.build(path, _tagged_filter_snapshot(), build_mode="full")
    expected = CatalogDatabase.query(path, tag_id=TAG_A, page_size=100)["items"]
    seen: list[dict] = []
    cursor = None
    while True:
        page = CatalogDatabase.query(path, tag_id=TAG_A, page_size=1, cursor=cursor)
        seen.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == expected
    assert len({item["item_id"] for item in seen}) == 4
    first_page = CatalogDatabase.query(path, tag_id=TAG_A, page_size=1)
    with pytest.raises(ResearchKBError) as mismatched_tag:
        CatalogDatabase.query(path, tag_id=TAG_B, page_size=1, cursor=first_page["next_cursor"])
    assert mismatched_tag.value.diagnostic.code == "RKBC-037"

    combined = CatalogDatabase.query(
        path,
        query="Alpha",
        item_kinds=("paper",),
        paper_id=PAPER_A,
        question_id=QUESTION_B,
        tag_id=TAG_A,
    )
    assert [item["title"] for item in combined["items"]] == ["Alpha two"]
    assert combined["tag_id"] == TAG_A
