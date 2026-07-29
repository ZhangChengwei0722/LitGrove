from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.catalog.models import CATALOG_CONTRACT_VERSION, CatalogDocument, CatalogSnapshot, canonical_digest
from research_kb.errors import Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, validate_id


CATALOG_PROJECTION_ERROR = "RKBC-036"
CATALOG_CURSOR_INVALID = "RKBC-037"
CATALOG_SCHEMA_VERSION = 1
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
    def build(path: Path, snapshot: CatalogSnapshot, *, build_mode: str) -> None:
        connection = _connect(path)
        try:
            _create_schema(connection)
            _replace_all(connection, snapshot, build_mode=build_mode)
        finally:
            connection.close()

    @staticmethod
    def update(path: Path, snapshot: CatalogSnapshot) -> dict[str, int]:
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
            removed = set(old_sources) - set(new_sources)
            documents_by_source: dict[str, list[CatalogDocument]] = {}
            for document in snapshot.documents:
                documents_by_source.setdefault(document.source_key, []).append(document)

            with connection:
                _delete_source_items(connection, changed)
                _delete_source_records(connection, changed)
                changed_sources = [
                    item for item in snapshot.source_records if item.source_key in changed
                ]
                connection.executemany(
                    """
                    INSERT INTO source_records(
                        source_key, record_kind, record_id, source_record_digest, adapter_version
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            item.source_key,
                            item.record_kind,
                            item.record_id,
                            item.source_record_digest,
                            item.adapter_version,
                        )
                        for item in changed_sources
                    ],
                )
                for source_key in sorted(changed & set(new_sources)):
                    for document in documents_by_source.get(source_key, []):
                        _insert_document(connection, document)
                _write_metadata(connection, snapshot, build_mode="incremental")
                _require_snapshot_counts(connection, snapshot)
            return {
                "changed_source_count": len(changed),
                "removed_source_count": len(removed),
                "item_count": len(snapshot.documents),
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
                count = int(connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0])
                return CatalogInspection("ready", _decode_metadata(metadata), count)
            finally:
                connection.close()
        except (OSError, sqlite3.DatabaseError, ValueError):
            return CatalogInspection("corrupt", {}, 0)

    @staticmethod
    def query(
        path: Path,
        *,
        query: str = "",
        item_kinds: tuple[str, ...] = (),
        paper_id: str | None = None,
        question_id: str | None = None,
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
        query_key = canonical_digest(
            {
                "query": normalized_query,
                "item_kinds": normalized_kinds,
                "paper_id": paper_id,
                "question_id": question_id,
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
                "items": [_row_to_item(row) for row in selected],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }
            if paper_id is not None:
                result["paper_id"] = paper_id
            if question_id is not None:
                result["question_id"] = question_id
            return result
        except sqlite3.DatabaseError as error:
            raise _projection_error("catalog query failed") from error
        finally:
            connection.close()

    @staticmethod
    def detail_row(path: Path, item_id: str) -> dict[str, Any] | None:
        connection = _connect(path, read_only=True)
        try:
            row = connection.execute(
                """
                SELECT item_id, item_kind, authority_layer, source_key, record_kind,
                       record_id, child_id, paper_id, question_id, title, summary,
                       status_labels, sort_key, source_record_digest, adapter_version
                FROM catalog_items WHERE item_id = ?
                """,
                (item_id,),
            ).fetchone()
            return None if row is None else _row_to_item(row, include_source=True)
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
        PRAGMA user_version = 1;
        CREATE TABLE catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE source_records (
            source_key TEXT PRIMARY KEY,
            record_kind TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_record_digest TEXT NOT NULL,
            adapter_version TEXT NOT NULL
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
        CREATE VIRTUAL TABLE catalog_fts USING fts5(
            item_id UNINDEXED,
            title,
            summary,
            search_text,
            tokenize = 'unicode61'
        );
        """
    )


def _replace_all(connection: sqlite3.Connection, snapshot: CatalogSnapshot, *, build_mode: str) -> None:
    with connection:
        connection.executemany(
            """
            INSERT INTO source_records(
                source_key, record_kind, record_id, source_record_digest, adapter_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    item.source_key,
                    item.record_kind,
                    item.record_id,
                    item.source_record_digest,
                    item.adapter_version,
                )
                for item in snapshot.source_records
            ],
        )
        for document in snapshot.documents:
            _insert_document(connection, document)
        _write_metadata(connection, snapshot, build_mode=build_mode)
        _require_snapshot_counts(connection, snapshot)


def _insert_document(connection: sqlite3.Connection, document: CatalogDocument) -> None:
    connection.execute(
        """
        INSERT INTO catalog_items(
            item_id, item_kind, authority_layer, source_key, record_kind, record_id,
            child_id, paper_id, question_id, title, summary, status_labels, search_text,
            sort_key, source_record_digest, adapter_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
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
        ),
    )
    connection.execute(
        "INSERT INTO catalog_fts(item_id, title, summary, search_text) VALUES (?, ?, ?, ?)",
        (document.item_id, document.title, document.summary, document.search_text),
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


def _write_metadata(connection: sqlite3.Connection, snapshot: CatalogSnapshot, *, build_mode: str) -> None:
    values = {
        "catalog_contract_version": CATALOG_CONTRACT_VERSION,
        "catalog_schema_version": str(CATALOG_SCHEMA_VERSION),
        "workspace_id": snapshot.workspace_id,
        "adapter_registry_version": snapshot.registry_version,
        "source_watermark": snapshot.source_watermark,
        "build_mode": build_mode,
        "source_record_count": str(len(snapshot.source_records)),
        "item_count": str(len(snapshot.documents)),
        "unknown_record_kinds": json.dumps(snapshot.unknown_record_kinds, separators=(",", ":")),
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
    actual = {
        "source_records": int(
            connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
        ),
        "catalog_items": int(connection.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0]),
        "catalog_fts": int(connection.execute("SELECT COUNT(*) FROM catalog_fts").fetchone()[0]),
    }
    expected = {
        "source_records": len(snapshot.source_records),
        "catalog_items": len(snapshot.documents),
        "catalog_fts": len(snapshot.documents),
    }
    if actual != expected or connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise _projection_error("catalog projection integrity does not match its source snapshot")


def _decode_metadata(metadata: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = dict(metadata)
    for key in ("catalog_schema_version", "source_record_count", "item_count"):
        if key in result:
            result[key] = int(result[key])
    if "unknown_record_kinds" in result:
        result["unknown_record_kinds"] = json.loads(result["unknown_record_kinds"])
    return result


def _row_to_item(row: sqlite3.Row, *, include_source: bool = False) -> dict[str, Any]:
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
    if include_source:
        value["source_key"] = row["source_key"]
    return value


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
