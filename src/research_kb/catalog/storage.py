from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping
from typing import Any

from research_kb.catalog.models import (
    CATALOG_CONTRACT_VERSION,
    CatalogDocument,
    CatalogSnapshot,
    CatalogSourceLocator,
    CatalogSourceRecord,
    canonical_digest,
)
from research_kb.errors import Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id


CATALOG_PROJECTION_ERROR = "RKBC-036"
CATALOG_CURSOR_INVALID = "RKBC-037"
CATALOG_SCHEMA_VERSION = 3
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_QUERY_CHARACTERS = 500
SQLITE_PARAMETER_BATCH_SIZE = 500


@dataclass(frozen=True, slots=True)
class CatalogInspection:
    state: str
    metadata: dict[str, Any]
    item_count: int


class CatalogDatabase:
    @staticmethod
    def build(
        path: Path,
        snapshot: CatalogSnapshot,
        *,
        build_mode: str,
        source_locators: tuple[CatalogSourceLocator, ...] = (),
        source_store_digests: Mapping[str, str] | None = None,
    ) -> None:
        connection = _connect(path)
        try:
            _create_schema(connection)
            _replace_all(
                connection,
                snapshot,
                build_mode=build_mode,
                source_locators=source_locators,
                source_store_digests=source_store_digests or {},
            )
        finally:
            connection.close()

    @staticmethod
    def update(
        path: Path,
        snapshot: CatalogSnapshot,
        *,
        source_locators: tuple[CatalogSourceLocator, ...] = (),
        source_store_digests: Mapping[str, str] | None = None,
    ) -> dict[str, int]:
        inspection = CatalogDatabase.inspect(path)
        if inspection.state != "ready":
            raise _projection_error("catalog projection is not compatible with incremental update")
        connection = _connect(path)
        try:
            old_metadata = _metadata(connection)
            if old_metadata.get("workspace_id") != snapshot.workspace_id:
                raise _projection_error(
                    "catalog projection workspace does not match the incremental snapshot"
                )
            old_registry_version = old_metadata.get("adapter_registry_version")
            old_sources = {
                row["source_key"]: (row["source_record_digest"], row["adapter_version"])
                for row in connection.execute(
                    "SELECT source_key, source_record_digest, adapter_version FROM source_records"
                )
            }
            new_sources = {
                item.source_key: (item.source_record_digest, item.adapter_version)
                for item in snapshot.source_records
            }
            if old_registry_version != snapshot.registry_version:
                changed = set(old_sources) | set(new_sources)
            else:
                changed = {
                    key
                    for key in set(old_sources) | set(new_sources)
                    if old_sources.get(key) != new_sources.get(key)
                }
            old_item_tags = _item_tag_index(connection)
            new_item_tags = {
                item.item_id: tuple(zip(item.tag_ids, item.tag_names, strict=True))
                for item in snapshot.documents
            }
            changed_item_ids = {
                item_id
                for item_id in set(old_item_tags) | set(new_item_tags)
                if old_item_tags.get(item_id, ()) != new_item_tags.get(item_id, ())
            }
            old_item_sources = {
                row["item_id"]: row["source_key"]
                for row in connection.execute("SELECT item_id, source_key FROM catalog_items")
            }
            new_item_sources = {item.item_id: item.source_key for item in snapshot.documents}
            changed.update(
                source_key
                for item_id in changed_item_ids
                for source_key in (old_item_sources.get(item_id), new_item_sources.get(item_id))
                if source_key is not None
            )
            removed = set(old_sources) - set(new_sources)
            documents_by_source: dict[str, list[CatalogDocument]] = {}
            for document in snapshot.documents:
                documents_by_source.setdefault(document.source_key, []).append(document)
            locators = {item.source_key: item for item in source_locators}

            with connection:
                _delete_source_items(connection, changed)
                _delete_source_records(connection, changed)
                changed_sources = [
                    item for item in snapshot.source_records if item.source_key in changed
                ]
                _insert_source_records(connection, changed_sources, locators)
                _update_source_locators(connection, locators)
                changed_documents = [
                    document
                    for source_key in sorted(changed & set(new_sources))
                    for document in documents_by_source.get(source_key, [])
                ]
                _insert_documents(connection, changed_documents)
                _write_metadata(
                    connection,
                    snapshot,
                    build_mode="incremental",
                    source_store_digests=source_store_digests or {},
                )
                _require_snapshot_counts(connection, snapshot)
            return {
                "changed_source_count": len(changed),
                "removed_source_count": len(removed),
                "item_count": len(snapshot.documents),
            }
        finally:
            connection.close()

    @staticmethod
    def update_registry_sources(
        path: Path,
        snapshot: CatalogSnapshot,
        *,
        source_locators: tuple[CatalogSourceLocator, ...],
        registry_store_digest: str,
        base_source_watermark: str,
        before_registry_store_digest: str,
    ) -> dict[str, Any]:
        inspection = CatalogDatabase.inspect(path)
        if inspection.state != "ready":
            raise _projection_error("catalog projection is not compatible with Registry delta")
        connection = _connect(path)
        try:
            metadata = _metadata(connection)
            if metadata.get("workspace_id") != snapshot.workspace_id:
                raise _projection_error("catalog projection workspace does not match Registry delta")
            if metadata.get("adapter_registry_version") != snapshot.registry_version:
                raise _projection_error("catalog adapter registry changed before Registry delta")
            if metadata.get("source_watermark") != base_source_watermark:
                raise _projection_error("catalog source watermark changed before Registry delta")
            store_digests = _decode_store_digests(metadata)
            if store_digests.get("registry") != before_registry_store_digest:
                raise _projection_error("Registry store digest does not match the projected base")
            if snapshot.unknown_record_kinds:
                raise _projection_error("Registry delta cannot carry unknown record kinds")
            if connection.execute("SELECT 1 FROM catalog_item_tags LIMIT 1").fetchone() is not None:
                raise _projection_error(
                    "Registry delta cannot preserve existing Tag facets; use a full Catalog update"
                )

            old_sources = {
                row["source_key"]: (row["source_record_digest"], row["adapter_version"])
                for row in connection.execute(
                    """
                    SELECT source_key, source_record_digest, adapter_version
                    FROM source_records WHERE record_kind = 'registry-paper'
                    """
                )
            }
            new_sources = {
                item.source_key: (item.source_record_digest, item.adapter_version)
                for item in snapshot.source_records
            }
            changed = {
                key
                for key in set(old_sources) | set(new_sources)
                if old_sources.get(key) != new_sources.get(key)
            }
            removed = set(old_sources) - set(new_sources)
            documents_by_source: dict[str, list[CatalogDocument]] = {}
            for document in snapshot.documents:
                documents_by_source.setdefault(document.source_key, []).append(document)
            locators = {item.source_key: item for item in source_locators}
            if set(locators) != set(new_sources):
                raise _projection_error("Registry delta locators do not match Registry sources")

            with connection:
                _delete_source_items(connection, changed)
                _delete_source_records(connection, changed)
                changed_sources = [
                    item for item in snapshot.source_records if item.source_key in changed
                ]
                _insert_source_records(connection, changed_sources, locators)
                _update_source_locators(connection, locators)
                changed_documents = [
                    document
                    for source_key in sorted(changed & set(new_sources))
                    for document in documents_by_source.get(source_key, [])
                ]
                _insert_documents(connection, changed_documents)
                source_watermark = _source_watermark(
                    connection,
                    registry_version=snapshot.registry_version,
                    unknown=(),
                )
                store_digests["registry"] = registry_store_digest
                counts = _actual_counts(connection)
                facet_count, facet_digest = _facet_integrity(connection)
                _write_metadata_values(
                    connection,
                    workspace_id=snapshot.workspace_id,
                    registry_version=snapshot.registry_version,
                    source_watermark=source_watermark,
                    build_mode="benchmark-registry-delta",
                    source_record_count=counts["source_records"],
                    item_count=counts["catalog_items"],
                    unknown_record_kinds=(),
                    source_store_digests=store_digests,
                    facet_count=facet_count,
                    facet_digest=facet_digest,
                )
                _require_counts(connection, counts)
            return {
                "status": "success",
                "build_mode": "benchmark-registry-delta",
                "workspace_id": snapshot.workspace_id,
                "source_watermark": source_watermark,
                "source_record_count": counts["source_records"],
                "item_count": counts["catalog_items"],
                "unknown_record_kinds": [],
                "changed_source_count": len(changed),
                "removed_source_count": len(removed),
            }
        finally:
            connection.close()

    @staticmethod
    def source_index(path: Path, *, record_kind: str) -> dict[str, tuple[str, str]]:
        connection = _connect(path, read_only=True)
        try:
            return {
                row["source_key"]: (
                    row["source_record_digest"],
                    row["adapter_version"],
                )
                for row in connection.execute(
                    """
                    SELECT source_key, source_record_digest, adapter_version
                    FROM source_records WHERE record_kind = ?
                    """,
                    (record_kind,),
                )
            }
        finally:
            connection.close()

    @staticmethod
    def inspect(path: Path) -> CatalogInspection:
        if not path.is_file():
            return CatalogInspection("missing", {}, 0)
        try:
            connection = _connect(path, read_only=True)
            try:
                integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
                if integrity != "ok":
                    return CatalogInspection("corrupt", {}, 0)
                metadata = _metadata(connection)
                if (
                    metadata.get("catalog_contract_version") != CATALOG_CONTRACT_VERSION
                    or int(metadata.get("catalog_schema_version", "-1")) != CATALOG_SCHEMA_VERSION
                ):
                    return CatalogInspection("incompatible", metadata, 0)
                facet_count, facet_digest = _facet_integrity(connection)
                if (
                    int(metadata["facet_count"]) != facet_count
                    or metadata["facet_digest"] != facet_digest
                ):
                    return CatalogInspection("corrupt", _decode_metadata(metadata), 0)
                count = int(connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0])
                return CatalogInspection("ready", _decode_metadata(metadata), count)
            finally:
                connection.close()
        except (KeyError, OSError, sqlite3.DatabaseError, ValueError):
            return CatalogInspection("corrupt", {}, 0)

    @staticmethod
    def query(
        path: Path,
        *,
        query: str = "",
        item_kinds: tuple[str, ...] = (),
        paper_id: str | None = None,
        question_id: str | None = None,
        tag_id: str | None = None,
        status_labels: tuple[str, ...] = (),
        page_size: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if len(normalized_query) > MAX_QUERY_CHARACTERS:
            raise _cursor_error("catalog query exceeds the character limit")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise _cursor_error("catalog page size is outside the supported range")
        normalized_kinds = tuple(sorted(set(item_kinds)))
        if paper_id is not None:
            validate_id(paper_id, Namespace.PAPER)
        if question_id is not None:
            validate_id(question_id, Namespace.QUESTION)
        if tag_id is not None:
            validate_id(tag_id, Namespace.TAG)
        normalized_statuses = tuple(sorted(set(status_labels)))
        if len(normalized_statuses) > 20 or any(not isinstance(item, str) or not item or len(item) > 200 for item in normalized_statuses):
            raise _cursor_error("catalog status-label filter is outside the supported bounds")
        query_key = canonical_digest(
            {
                "query": normalized_query,
                "item_kinds": normalized_kinds,
                "paper_id": paper_id,
                "question_id": question_id,
                "tag_id": tag_id,
                "status_labels": normalized_statuses,
                "order": "sort_key,item_kind,item_id",
            }
        )
        after = _decode_cursor(cursor, query_key) if cursor else None
        connection = _connect(path, read_only=True)
        try:
            if after is not None and not _cursor_position_exists(connection, after):
                raise _cursor_error("catalog cursor position is no longer available")
            sql = """
                SELECT item_id, item_kind, authority_layer, record_kind, record_id,
                       child_id, paper_id, question_id, title, summary, status_labels,
                       sort_key, source_record_digest, adapter_version
                FROM catalog_items
                WHERE 1 = 1
            """
            parameters: list[Any] = []
            if normalized_query:
                sql += " AND item_id IN (SELECT item_id FROM catalog_fts WHERE catalog_fts MATCH ?)"
                parameters.append(_fts_query(normalized_query))
            if normalized_kinds:
                placeholders = ",".join("?" for _ in normalized_kinds)
                sql += f" AND item_kind IN ({placeholders})"
                parameters.extend(normalized_kinds)
            if paper_id is not None:
                sql += " AND paper_id = ?"
                parameters.append(paper_id)
            if question_id is not None:
                sql += " AND question_id = ?"
                parameters.append(question_id)
            if tag_id is not None:
                sql += " AND EXISTS (SELECT 1 FROM catalog_item_tags AS tags WHERE tags.item_id = catalog_items.item_id AND tags.tag_id = ?)"
                parameters.append(tag_id)
            for status_label in normalized_statuses:
                sql += " AND EXISTS (SELECT 1 FROM json_each(catalog_items.status_labels) AS labels WHERE labels.value = ?)"
                parameters.append(status_label)
            if after is not None:
                sql += """
                    AND (
                        sort_key > ?
                        OR (sort_key = ? AND item_kind > ?)
                        OR (sort_key = ? AND item_kind = ? AND item_id > ?)
                    )
                """
                parameters.extend(
                    [after[0], after[0], after[1], after[0], after[1], after[2]]
                )
            sql += " ORDER BY sort_key, item_kind, item_id LIMIT ?"
            parameters.append(page_size + 1)
            rows = connection.execute(sql, parameters).fetchall()
            has_more = len(rows) > page_size
            selected = rows[:page_size]
            tag_index = _item_tags_for_ids(
                connection,
                tuple(row["item_id"] for row in selected),
            )
            next_cursor = None
            if has_more and selected:
                last = selected[-1]
                next_cursor = _encode_cursor(
                    query_key,
                    (last["sort_key"], last["item_kind"], last["item_id"]),
                )
            result = {
                "status": "success",
                "query": normalized_query,
                "item_kinds": list(normalized_kinds),
                "page_size": page_size,
                "items": [
                    _row_to_item(row, tags=tag_index.get(row["item_id"], ()))
                    for row in selected
                ],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
            if paper_id is not None:
                result["paper_id"] = paper_id
            if question_id is not None:
                result["question_id"] = question_id
            if tag_id is not None:
                result["tag_id"] = tag_id
            if normalized_statuses:
                result["status_labels"] = list(normalized_statuses)
            return result
        except sqlite3.DatabaseError as error:
            raise _projection_error("catalog query failed") from error
        finally:
            connection.close()

    @staticmethod
    def detail_row(path: Path, item_id: str) -> dict[str, Any] | None:
        binding = CatalogDatabase.detail_binding(path, item_id)
        return None if binding is None else binding["item"]

    @staticmethod
    def detail_binding(path: Path, item_id: str) -> dict[str, Any] | None:
        connection = _connect(path, read_only=True)
        try:
            row = connection.execute(
                """
                SELECT i.item_id, i.item_kind, i.authority_layer, i.source_key,
                       i.record_kind, i.record_id, i.child_id, i.paper_id,
                       i.question_id, i.title, i.summary, i.status_labels, i.sort_key,
                       i.source_record_digest, i.adapter_version, s.store_key,
                       s.byte_offset, s.byte_length
                FROM catalog_items AS i
                JOIN source_records AS s ON s.source_key = i.source_key
                WHERE i.item_id = ?
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                return None
            locator = None
            if row["store_key"] is not None:
                locator = {
                    "store_key": row["store_key"],
                    "byte_offset": row["byte_offset"],
                    "byte_length": row["byte_length"],
                }
            return {
                "item": _row_to_item(row, include_source=True, connection=connection),
                "locator": locator,
            }
        finally:
            connection.close()

    @staticmethod
    def detail_bindings(path: Path, item_ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
        if not item_ids:
            return {}
        if len(item_ids) > MAX_PAGE_SIZE or len(set(item_ids)) != len(item_ids):
            raise _cursor_error("catalog detail binding batch is outside the supported bounds")
        connection = _connect(path, read_only=True)
        try:
            placeholders = ",".join("?" for _ in item_ids)
            rows = connection.execute(
                f"""
                SELECT i.item_id, i.item_kind, i.authority_layer, i.source_key,
                       i.record_kind, i.record_id, i.child_id, i.paper_id,
                       i.question_id, i.title, i.summary, i.status_labels, i.sort_key,
                       i.source_record_digest, i.adapter_version, s.store_key,
                       s.byte_offset, s.byte_length
                FROM catalog_items AS i
                JOIN source_records AS s ON s.source_key = i.source_key
                WHERE i.item_id IN ({placeholders})
                """,
                item_ids,
            ).fetchall()
            tag_index = _item_tags_for_ids(
                connection,
                tuple(row["item_id"] for row in rows),
            )
            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                locator = None
                if row["store_key"] is not None:
                    locator = {
                        "store_key": row["store_key"],
                        "byte_offset": row["byte_offset"],
                        "byte_length": row["byte_length"],
                    }
                result[row["item_id"]] = {
                    "item": _row_to_item(
                        row,
                        include_source=True,
                        tags=tag_index.get(row["item_id"], ()),
                    ),
                    "locator": locator,
                }
            return result
        except sqlite3.DatabaseError as error:
            raise _projection_error("catalog detail binding batch failed") from error
        finally:
            connection.close()

    @staticmethod
    def late_cursor(path: Path, *, item_kind: str, page_size: int) -> str | None:
        if not item_kind or page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise _cursor_error("catalog late cursor request is outside the supported bounds")
        query_key = canonical_digest(
            {
                "query": "",
                "item_kinds": [item_kind],
                "paper_id": None,
                "question_id": None,
                "tag_id": None,
                "status_labels": [],
                "order": "sort_key,item_kind,item_id",
            }
        )
        connection = _connect(path, read_only=True)
        try:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_items WHERE item_kind = ?",
                    (item_kind,),
                ).fetchone()[0]
            )
            if count <= page_size:
                return None
            row = connection.execute(
                """
                SELECT sort_key, item_kind, item_id
                FROM catalog_items
                WHERE item_kind = ?
                ORDER BY sort_key, item_kind, item_id
                LIMIT 1 OFFSET ?
                """,
                (item_kind, count - page_size - 1),
            ).fetchone()
            if row is None:
                raise _cursor_error("catalog late cursor position is unavailable")
            return _encode_cursor(query_key, (row["sort_key"], row["item_kind"], row["item_id"]))
        except sqlite3.DatabaseError as error:
            raise _projection_error("catalog late cursor query failed") from error
        finally:
            connection.close()


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA user_version = 3;
        CREATE TABLE catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE source_records (
            source_key TEXT PRIMARY KEY,
            record_kind TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_record_digest TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            store_key TEXT,
            byte_offset INTEGER,
            byte_length INTEGER
        );
        CREATE TABLE catalog_items (
            item_id TEXT PRIMARY KEY,
            item_kind TEXT NOT NULL,
            authority_layer TEXT NOT NULL,
            source_key TEXT NOT NULL REFERENCES source_records(source_key) ON DELETE CASCADE,
            record_kind TEXT NOT NULL,
            record_id TEXT NOT NULL,
            child_id TEXT,
            paper_id TEXT,
            question_id TEXT,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            status_labels TEXT NOT NULL,
            search_text TEXT NOT NULL,
            sort_key TEXT NOT NULL,
            source_record_digest TEXT NOT NULL,
            adapter_version TEXT NOT NULL
        );
        CREATE INDEX catalog_items_order ON catalog_items(sort_key, item_kind, item_id);
        CREATE INDEX catalog_items_kind ON catalog_items(item_kind, sort_key, item_id);
        CREATE INDEX catalog_items_source ON catalog_items(source_key);
        CREATE INDEX catalog_items_paper ON catalog_items(paper_id);
        CREATE INDEX catalog_items_question ON catalog_items(question_id);
        CREATE TABLE catalog_item_tags (
            item_id TEXT NOT NULL REFERENCES catalog_items(item_id) ON DELETE CASCADE,
            tag_id TEXT NOT NULL,
            tag_name TEXT NOT NULL,
            PRIMARY KEY (item_id, tag_id)
        );
        CREATE INDEX catalog_item_tags_tag ON catalog_item_tags(tag_id, item_id);
        CREATE VIRTUAL TABLE catalog_fts USING fts5(
            item_id UNINDEXED,
            title,
            summary,
            search_text,
            tokenize = 'unicode61'
        );
        """
    )


def _replace_all(
    connection: sqlite3.Connection,
    snapshot: CatalogSnapshot,
    *,
    build_mode: str,
    source_locators: tuple[CatalogSourceLocator, ...],
    source_store_digests: Mapping[str, str],
) -> None:
    locators = {item.source_key: item for item in source_locators}
    with connection:
        _insert_source_records(connection, snapshot.source_records, locators)
        _insert_documents(connection, snapshot.documents)
        _write_metadata(
            connection,
            snapshot,
            build_mode=build_mode,
            source_store_digests=source_store_digests,
        )
        _require_snapshot_counts(connection, snapshot)


def _insert_source_records(
    connection: sqlite3.Connection,
    sources: list[CatalogSourceRecord] | tuple[CatalogSourceRecord, ...],
    locators: Mapping[str, CatalogSourceLocator],
) -> None:
    connection.executemany(
        """
        INSERT INTO source_records(
            source_key, record_kind, record_id, source_record_digest, adapter_version,
            store_key, byte_offset, byte_length
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.source_key,
                item.record_kind,
                item.record_id,
                item.source_record_digest,
                item.adapter_version,
                locators[item.source_key].store_key if item.source_key in locators else None,
                locators[item.source_key].byte_offset if item.source_key in locators else None,
                locators[item.source_key].byte_length if item.source_key in locators else None,
            )
            for item in sources
        ],
    )


def _update_source_locators(
    connection: sqlite3.Connection,
    locators: Mapping[str, CatalogSourceLocator],
) -> None:
    connection.executemany(
        """
        UPDATE source_records
        SET store_key = ?, byte_offset = ?, byte_length = ?
        WHERE source_key = ?
        """,
        [
            (item.store_key, item.byte_offset, item.byte_length, item.source_key)
            for item in locators.values()
        ],
    )


def _insert_documents(
    connection: sqlite3.Connection,
    documents: list[CatalogDocument] | tuple[CatalogDocument, ...],
) -> None:
    connection.executemany(
        """
        INSERT INTO catalog_items(
            item_id, item_kind, authority_layer, source_key, record_kind, record_id,
            child_id, paper_id, question_id, title, summary, status_labels, search_text,
            sort_key, source_record_digest, adapter_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (_document_row(document) for document in documents),
    )
    connection.executemany(
        "INSERT INTO catalog_item_tags(item_id, tag_id, tag_name) VALUES (?, ?, ?)",
        (
            (document.item_id, tag_id, tag_name)
            for document in documents
            for tag_id, tag_name in zip(document.tag_ids, document.tag_names, strict=True)
        ),
    )
    connection.executemany(
        "INSERT INTO catalog_fts(item_id, title, summary, search_text) VALUES (?, ?, ?, ?)",
        (
            (document.item_id, document.title, document.summary, document.search_text)
            for document in documents
        ),
    )


def _document_row(document: CatalogDocument) -> tuple[Any, ...]:
    return (
        document.item_id,
        document.item_kind,
        document.authority_layer,
        document.source_key,
        document.record_kind,
        document.record_id,
        document.child_id,
        document.paper_id,
        document.question_id,
        document.title,
        document.summary,
        json.dumps(document.status_labels, ensure_ascii=False, separators=(",", ":")),
        document.search_text,
        document.sort_key,
        document.source_record_digest,
        document.adapter_version,
    )


def _delete_source_items(connection: sqlite3.Connection, source_keys: set[str]) -> None:
    for source_batch in _batches(sorted(source_keys)):
        placeholders = ",".join("?" for _ in source_batch)
        item_ids = [
            row[0]
            for row in connection.execute(
                f"SELECT item_id FROM catalog_items WHERE source_key IN ({placeholders})",
                source_batch,
            )
        ]
        for item_batch in _batches(item_ids):
            item_placeholders = ",".join("?" for _ in item_batch)
            connection.execute(
                f"DELETE FROM catalog_fts WHERE item_id IN ({item_placeholders})",
                item_batch,
            )
        connection.execute(
            f"DELETE FROM catalog_items WHERE source_key IN ({placeholders})",
            source_batch,
        )


def _delete_source_records(connection: sqlite3.Connection, source_keys: set[str]) -> None:
    for source_batch in _batches(sorted(source_keys)):
        placeholders = ",".join("?" for _ in source_batch)
        connection.execute(
            f"DELETE FROM source_records WHERE source_key IN ({placeholders})",
            source_batch,
        )


def _batches(values: list[str]) -> list[list[str]]:
    return [
        values[index : index + SQLITE_PARAMETER_BATCH_SIZE]
        for index in range(0, len(values), SQLITE_PARAMETER_BATCH_SIZE)
    ]


def _write_metadata(
    connection: sqlite3.Connection,
    snapshot: CatalogSnapshot,
    *,
    build_mode: str,
    source_store_digests: Mapping[str, str],
) -> None:
    facet_count, facet_digest = _snapshot_facet_integrity(snapshot)
    _write_metadata_values(
        connection,
        workspace_id=snapshot.workspace_id,
        registry_version=snapshot.registry_version,
        source_watermark=snapshot.source_watermark,
        build_mode=build_mode,
        source_record_count=len(snapshot.source_records),
        item_count=len(snapshot.documents),
        unknown_record_kinds=snapshot.unknown_record_kinds,
        source_store_digests=source_store_digests,
        facet_count=facet_count,
        facet_digest=facet_digest,
    )


def _write_metadata_values(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    registry_version: str,
    source_watermark: str,
    build_mode: str,
    source_record_count: int,
    item_count: int,
    unknown_record_kinds: tuple[str, ...],
    source_store_digests: Mapping[str, str],
    facet_count: int,
    facet_digest: str,
) -> None:
    values = {
        "catalog_contract_version": CATALOG_CONTRACT_VERSION,
        "catalog_schema_version": str(CATALOG_SCHEMA_VERSION),
        "workspace_id": workspace_id,
        "adapter_registry_version": registry_version,
        "source_watermark": source_watermark,
        "build_mode": build_mode,
        "source_record_count": str(source_record_count),
        "item_count": str(item_count),
        "unknown_record_kinds": json.dumps(unknown_record_kinds, separators=(",", ":")),
        "source_store_digests": json.dumps(
            dict(sorted(source_store_digests.items())), separators=(",", ":")
        ),
        "facet_count": str(facet_count),
        "facet_digest": facet_digest,
    }
    connection.execute("DELETE FROM catalog_metadata")
    connection.executemany(
        "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)",
        sorted(values.items()),
    )


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM catalog_metadata")
    }


def _require_snapshot_counts(
    connection: sqlite3.Connection,
    snapshot: CatalogSnapshot,
) -> None:
    actual = _actual_counts(connection)
    expected = {
        "source_records": len(snapshot.source_records),
        "catalog_items": len(snapshot.documents),
        "catalog_fts": len(snapshot.documents),
        "catalog_item_tags": sum(len(document.tag_ids) for document in snapshot.documents),
    }
    if actual != expected:
        raise _projection_error("catalog projection integrity does not match its source snapshot")
    if _facet_integrity(connection) != _snapshot_facet_integrity(snapshot):
        raise _projection_error("catalog Tag facet integrity does not match its source snapshot")
    _require_counts(connection, actual)


def _actual_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        "source_records": int(
            connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        ),
        "catalog_items": int(connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0]),
        "catalog_fts": int(connection.execute("SELECT COUNT(*) FROM catalog_fts").fetchone()[0]),
        "catalog_item_tags": int(
            connection.execute("SELECT COUNT(*) FROM catalog_item_tags").fetchone()[0]
        ),
    }


def _require_counts(connection: sqlite3.Connection, counts: Mapping[str, int]) -> None:
    if (
        counts["catalog_items"] != counts["catalog_fts"]
        or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
    ):
        raise _projection_error("catalog projection integrity does not match its source snapshot")


def _source_watermark(
    connection: sqlite3.Connection,
    *,
    registry_version: str,
    unknown: tuple[tuple[str, str], ...],
) -> str:
    indexed = [
        [row["source_key"], row["source_record_digest"], row["adapter_version"]]
        for row in connection.execute(
            """
            SELECT source_key, source_record_digest, adapter_version
            FROM source_records ORDER BY source_key
            """
        )
    ]
    return canonical_digest(
        {
            "registry_version": registry_version,
            "indexed": indexed,
            "unknown": list(unknown),
        }
    )


def _decode_store_digests(metadata: Mapping[str, str]) -> dict[str, str]:
    value = metadata.get("source_store_digests", "{}")
    decoded = json.loads(value)
    if not isinstance(decoded, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in decoded.items()
    ):
        raise _projection_error("catalog source-store digests are invalid")
    return decoded


def _decode_metadata(metadata: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = dict(metadata)
    for key in ("catalog_schema_version", "source_record_count", "item_count", "facet_count"):
        if key in result:
            result[key] = int(result[key])
    if "unknown_record_kinds" in result:
        result["unknown_record_kinds"] = json.loads(result["unknown_record_kinds"])
    if "source_store_digests" in result:
        result["source_store_digests"] = _decode_store_digests(metadata)
    return result


def _row_to_item(
    row: sqlite3.Row,
    *,
    include_source: bool = False,
    connection: sqlite3.Connection | None = None,
    tags: tuple[sqlite3.Row, ...] | list[sqlite3.Row] | None = None,
) -> dict[str, Any]:
    keys = (
        "item_id",
        "item_kind",
        "authority_layer",
        "record_kind",
        "record_id",
        "child_id",
        "paper_id",
        "question_id",
        "title",
        "summary",
        "source_record_digest",
        "adapter_version",
    )
    value = {key: row[key] for key in keys}
    value["status_labels"] = json.loads(row["status_labels"])
    selected_tags = tags
    if selected_tags is None and connection is not None:
        selected_tags = connection.execute(
            "SELECT tag_id, tag_name FROM catalog_item_tags WHERE item_id = ? ORDER BY tag_id",
            (row["item_id"],),
        ).fetchall()
    value["tags"] = [
        {"tag_id": item["tag_id"], "name": item["tag_name"]}
        for item in (selected_tags or ())
    ]
    if include_source:
        value["source_key"] = row["source_key"]
    return value


def _item_tags_for_ids(
    connection: sqlite3.Connection,
    item_ids: tuple[str, ...],
) -> dict[str, tuple[sqlite3.Row, ...]]:
    if not item_ids:
        return {}
    placeholders = ",".join("?" for _ in item_ids)
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in connection.execute(
        f"""
        SELECT item_id, tag_id, tag_name
        FROM catalog_item_tags
        WHERE item_id IN ({placeholders})
        ORDER BY item_id, tag_id
        """,
        item_ids,
    ):
        grouped.setdefault(row["item_id"], []).append(row)
    return {item_id: tuple(values) for item_id, values in grouped.items()}


def _item_tag_index(connection: sqlite3.Connection) -> dict[str, tuple[tuple[str, str], ...]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for row in connection.execute(
        "SELECT item_id, tag_id, tag_name FROM catalog_item_tags ORDER BY item_id, tag_id"
    ):
        grouped.setdefault(row["item_id"], []).append((row["tag_id"], row["tag_name"]))
    return {item_id: tuple(tags) for item_id, tags in grouped.items()}


def _facet_integrity(connection: sqlite3.Connection) -> tuple[int, str]:
    rows = [
        [row["item_id"], row["tag_id"], row["tag_name"]]
        for row in connection.execute(
            "SELECT item_id, tag_id, tag_name FROM catalog_item_tags ORDER BY item_id, tag_id"
        )
    ]
    return len(rows), canonical_digest(rows)


def _snapshot_facet_integrity(snapshot: CatalogSnapshot) -> tuple[int, str]:
    rows = sorted(
        [document.item_id, tag_id, tag_name]
        for document in snapshot.documents
        for tag_id, tag_name in zip(document.tag_ids, document.tag_names, strict=True)
    )
    return len(rows), canonical_digest(rows)


def _fts_query(query: str) -> str:
    terms = [item for item in query.split() if item]
    if not terms:
        raise _cursor_error("catalog query contains no searchable term")
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def _cursor_position_exists(
    connection: sqlite3.Connection,
    position: tuple[str, str, str],
) -> bool:
    return (
        connection.execute(
            """
            SELECT 1 FROM catalog_items
            WHERE sort_key = ? AND item_kind = ? AND item_id = ?
            """,
            position,
        ).fetchone()
        is not None
    )


def _encode_cursor(query_key: str, last: tuple[str, str, str]) -> str:
    content = json.dumps(
        {"version": 1, "query_key": query_key, "last": last},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, query_key: str) -> tuple[str, str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
        if value.get("version") != 1 or value.get("query_key") != query_key:
            raise ValueError
        last = value.get("last")
        if not isinstance(last, list) or len(last) != 3 or not all(isinstance(item, str) for item in last):
            raise ValueError
        return last[0], last[1], last[2]
    except (
        binascii.Error,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        TypeError,
    ) as error:
        raise _cursor_error("catalog cursor is invalid for this query") from error


def _projection_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(CATALOG_PROJECTION_ERROR, "catalog-projection", None, "", message)
    )


def _cursor_error(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(CATALOG_CURSOR_INVALID, "catalog-query", None, "/cursor", message)
    )


__all__ = [
    "CATALOG_CURSOR_INVALID",
    "CATALOG_PROJECTION_ERROR",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "CatalogDatabase",
    "CatalogInspection",
]
