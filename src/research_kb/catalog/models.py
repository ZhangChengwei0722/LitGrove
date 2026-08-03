from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


CATALOG_CONTRACT_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class CatalogSourceRecord:
    source_key: str
    record_kind: str
    record_id: str
    source_record_digest: str
    adapter_version: str


@dataclass(frozen=True, slots=True)
class CatalogSourceLocator:
    source_key: str
    store_key: str
    byte_offset: int
    byte_length: int


@dataclass(frozen=True, slots=True)
class CatalogDocument:
    item_id: str
    item_kind: str
    authority_layer: str
    source_key: str
    record_kind: str
    record_id: str
    child_id: str | None
    paper_id: str | None
    question_id: str | None
    title: str
    summary: str
    status_labels: tuple[str, ...]
    search_text: str
    sort_key: str
    source_record_digest: str
    adapter_version: str
    tag_ids: tuple[str, ...] = ()
    tag_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    workspace_id: str
    registry_version: str
    source_watermark: str
    source_records: tuple[CatalogSourceRecord, ...]
    documents: tuple[CatalogDocument, ...]
    unknown_record_kinds: tuple[str, ...]


def canonical_digest(value: Any) -> str:
    content = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(content).hexdigest()


__all__ = [
    "CATALOG_CONTRACT_VERSION",
    "CatalogDocument",
    "CatalogSnapshot",
    "CatalogSourceLocator",
    "CatalogSourceRecord",
    "canonical_digest",
]
