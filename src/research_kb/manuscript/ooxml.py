from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, LargeZipFile, ZipFile

from research_kb.errors import (
    INPUT_TOO_LARGE,
    MANUSCRIPT_SOURCE_UNSUPPORTED,
    Diagnostic,
    ResearchKBError,
)


MAX_ARCHIVE_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{WORD_NAMESPACE}}}"
DOCUMENT_PART = "word/document.xml"
STYLES_PART = "word/styles.xml"
PARSER_VERSION = "1.0"
DOCX_COVERAGE_LIMITS = [
    "comments_headers_footers_footnotes_endnotes_not_read",
    "deleted_revisions_not_read",
    "text_boxes_drawings_images_embedded_objects_not_read",
    "external_relationships_not_followed",
    "macros_not_executed",
]
_SKIPPED_TEXT_CONTAINERS = {
    f"{W}del",
    f"{W}moveFrom",
    f"{W}drawing",
    f"{W}pict",
    f"{W}object",
    f"{W}txbxContent",
}
_BLOCK_WRAPPERS = {
    f"{W}sdt",
    f"{W}sdtContent",
    f"{W}customXml",
    f"{W}ins",
    f"{W}moveTo",
}


class OoxmlManuscriptAdapter:
    name = "ooxml-stdlib"
    version = PARSER_VERSION

    def project(self, source: Path) -> dict[str, Any]:
        document_root, styles = _read_package(source)
        if document_root.tag != f"{W}document":
            raise _unsupported("word/document.xml has an unsupported root element")
        body = document_root.find(f"{W}body")
        if body is None:
            raise _unsupported("OOXML document has no WordprocessingML body")

        collector = _UnitCollector(styles)
        collector.walk_blocks(body, container=_body_container())
        if not collector.units:
            raise _unsupported("OOXML manuscript has no extractable visible paragraph text")
        return {
            "parser": {"adapter": self.name, "version": self.version},
            "unit_kind": "paragraph",
            "coverage_limits": list(DOCX_COVERAGE_LIMITS),
            "units": collector.units,
        }


class _UnitCollector:
    def __init__(self, styles: dict[str, dict[str, Any]]):
        self.styles = styles
        self.units: list[dict[str, Any]] = []
        self.table_count = 0

    def walk_blocks(self, parent: ElementTree.Element, *, container: dict[str, Any]) -> None:
        for child in parent:
            if child.tag == f"{W}p":
                self._append_paragraph(child, container)
            elif child.tag == f"{W}tbl":
                self._walk_table(child)
            elif child.tag in _BLOCK_WRAPPERS:
                self.walk_blocks(child, container=container)

    def _walk_table(self, table: ElementTree.Element) -> None:
        self.table_count += 1
        table_index = self.table_count
        rows = [child for child in table if child.tag == f"{W}tr"]
        for row_index, row in enumerate(rows, start=1):
            cells = [child for child in row if child.tag == f"{W}tc"]
            for cell_index, cell in enumerate(cells, start=1):
                container = {
                    "kind": "table",
                    "table_index": table_index,
                    "row_index": row_index,
                    "cell_index": cell_index,
                }
                self.walk_blocks(cell, container=container)

    def _append_paragraph(self, paragraph: ElementTree.Element, container: dict[str, Any]) -> None:
        text = _visible_paragraph_text(paragraph)
        if not text.strip():
            return
        style_id = _paragraph_style_id(paragraph)
        style = self.styles.get(style_id, {}) if style_id is not None else {}
        heading_level = _paragraph_outline_level(paragraph)
        if heading_level is None:
            heading_level = style.get("heading_level")
        if heading_level is None:
            heading_level = _heading_level_from_style(style_id, style.get("name"))
        unit_index = len(self.units) + 1
        self.units.append(
            {
                "unit_index": unit_index,
                "locator": f"docx:paragraph:{unit_index}",
                "text": text,
                "heading_level": heading_level,
                "style_id": style_id,
                "style_name": style.get("name"),
                "container": dict(container),
            }
        )


def _read_package(source: Path) -> tuple[ElementTree.Element, dict[str, dict[str, Any]]]:
    try:
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise _too_large("OOXML archive contains too many entries")
            if sum(item.file_size for item in entries) > MAX_UNCOMPRESSED_BYTES:
                raise _too_large("OOXML archive exceeds the uncompressed-size limit")
            if any(item.flag_bits & 0x1 for item in entries):
                raise _unsupported("encrypted OOXML packages are unsupported")
            names = [item.filename for item in entries]
            if names.count(DOCUMENT_PART) != 1:
                raise _unsupported("OOXML package must contain exactly one word/document.xml part")
            if names.count(STYLES_PART) > 1:
                raise _unsupported("OOXML package contains duplicate word/styles.xml parts")
            document_xml = archive.read(DOCUMENT_PART)
            styles_xml = archive.read(STYLES_PART) if STYLES_PART in names else None
    except ResearchKBError:
        raise
    except (BadZipFile, LargeZipFile, KeyError, OSError, RuntimeError) as error:
        raise _unsupported("manuscript is not a readable OOXML package") from error

    document_root = _parse_xml(document_xml, "word/document.xml")
    styles = _parse_styles(styles_xml) if styles_xml is not None else {}
    return document_root, styles


def _parse_xml(content: bytes, part_name: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError) as error:
        raise _unsupported(f"OOXML part {part_name} is malformed") from error


def _parse_styles(content: bytes) -> dict[str, dict[str, Any]]:
    root = _parse_xml(content, STYLES_PART)
    if root.tag != f"{W}styles":
        raise _unsupported("word/styles.xml has an unsupported root element")
    styles: dict[str, dict[str, Any]] = {}
    for style in root.findall(f"{W}style"):
        if style.get(f"{W}type") != "paragraph":
            continue
        style_id = style.get(f"{W}styleId")
        if not style_id:
            continue
        name_node = style.find(f"{W}name")
        styles[style_id] = {
            "name": name_node.get(f"{W}val") if name_node is not None else None,
            "heading_level": _outline_level(style.find(f"{W}pPr/{W}outlineLvl")),
        }
    return styles


def _visible_paragraph_text(paragraph: ElementTree.Element) -> str:
    parts: list[str] = []

    def visit(node: ElementTree.Element) -> None:
        if node.tag in _SKIPPED_TEXT_CONTAINERS:
            return
        if node.tag == f"{W}t":
            parts.append(node.text or "")
            return
        if node.tag == f"{W}tab":
            parts.append("\t")
            return
        if node.tag in {f"{W}br", f"{W}cr"}:
            parts.append("\n")
            return
        if node.tag == f"{W}noBreakHyphen":
            parts.append("-")
            return
        if node.tag == f"{W}softHyphen":
            parts.append("\u00ad")
            return
        for child in node:
            visit(child)

    visit(paragraph)
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n")


def _paragraph_style_id(paragraph: ElementTree.Element) -> str | None:
    style = paragraph.find(f"{W}pPr/{W}pStyle")
    return style.get(f"{W}val") if style is not None else None


def _paragraph_outline_level(paragraph: ElementTree.Element) -> int | None:
    return _outline_level(paragraph.find(f"{W}pPr/{W}outlineLvl"))


def _outline_level(node: ElementTree.Element | None) -> int | None:
    if node is None:
        return None
    value = node.get(f"{W}val")
    try:
        parsed = int(value) if value is not None else None
    except ValueError:
        return None
    return parsed + 1 if parsed is not None and 0 <= parsed <= 8 else None


def _heading_level_from_style(style_id: str | None, style_name: str | None) -> int | None:
    for value in (style_id, style_name):
        if not value:
            continue
        match = re.fullmatch(r"heading[ _-]*([1-9])", value.strip(), flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _body_container() -> dict[str, Any]:
    return {
        "kind": "body",
        "table_index": None,
        "row_index": None,
        "cell_index": None,
    }


def _unsupported(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            MANUSCRIPT_SOURCE_UNSUPPORTED,
            "manuscript-projection",
            None,
            "/source",
            message,
        )
    )


def _too_large(message: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(INPUT_TOO_LARGE, "manuscript-projection", None, "/source", message)
    )


__all__ = ["OoxmlManuscriptAdapter"]
