from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from research_kb.errors import JSONL_FORMAT_ERROR, Diagnostic, ResearchKBError


UTF8_BOM = b"\xef\xbb\xbf"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_sha256(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def read_json_document(path: Path, *, record_kind: str = "json", missing_ok: bool = False) -> dict[str, Any]:
    if not path.exists() and missing_ok:
        return {}
    text = _strict_text(path, record_kind)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise _format_error(record_kind, path, f"invalid JSON at line {error.lineno}, column {error.colno}") from error
    if not isinstance(value, dict):
        raise _format_error(record_kind, path, "JSON document root must be an object")
    return value


def read_jsonl(
    path: Path,
    *,
    record_kind: str = "jsonl",
    missing_ok: bool = True,
    id_field: str | None = None,
) -> list[dict[str, Any]]:
    if not path.exists() and missing_ok:
        return []
    text = _strict_text(path, record_kind)
    if not text:
        return []
    if not text.endswith("\n"):
        raise _format_error(record_kind, path, "JSONL must end with LF")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text[:-1].split("\n"), start=1):
        if not line:
            raise _format_error(record_kind, path, f"blank JSONL line at {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise _format_error(record_kind, path, f"invalid JSONL at line {line_number}, column {error.colno}") from error
        if not isinstance(value, dict):
            raise _format_error(record_kind, path, f"JSONL line {line_number} must be an object")
        if id_field is not None:
            identifier = value.get(id_field)
            if not isinstance(identifier, str) or not identifier:
                raise _format_error(record_kind, path, f"JSONL line {line_number} lacks {id_field}")
            if identifier in seen:
                raise _format_error(record_kind, path, f"duplicate {id_field} at line {line_number}")
            seen.add(identifier)
        records.append(value)
    return records


def serialize_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def serialize_jsonl(records: Iterable[dict[str, Any]]) -> bytes:
    lines = [json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for item in records]
    return (("\n".join(lines) + "\n") if lines else "").encode("utf-8")


def write_fsynced_temp(target: Path, content: bytes, write_id: str) -> Path:
    ensure_private_directory(target.parent)
    temporary = target.parent / f".{target.name}.{write_id}.tmp"
    target_mode = stat.S_IMODE(target.stat().st_mode) if os.name == "posix" and target.exists() else 0o600
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        if os.name == "posix":
            os.fchmod(handle.fileno(), target_mode)
        os.fsync(handle.fileno())
    return temporary


def ensure_private_directory(path: Path) -> None:
    existed = path.exists()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix" and not existed:
        path.chmod(0o700)


def replace_temp(temporary: Path, target: Path) -> None:
    os.replace(temporary, target)


def atomic_write_bytes(target: Path, content: bytes, write_id: str) -> None:
    temporary = write_fsynced_temp(target, content, write_id)
    try:
        replace_temp(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _strict_text(path: Path, record_kind: str) -> str:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise _format_error(record_kind, path, f"cannot read structured file: {error}") from error
    if content.startswith(UTF8_BOM):
        raise _format_error(record_kind, path, "UTF-8 BOM is not permitted")
    if b"\r" in content:
        raise _format_error(record_kind, path, "canonical structured files must use LF line endings")
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _format_error(record_kind, path, f"invalid UTF-8 at byte {error.start}") from error


def _format_error(record_kind: str, path: Path, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(JSONL_FORMAT_ERROR, record_kind, None, str(path), message))
