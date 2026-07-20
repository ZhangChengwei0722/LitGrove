from __future__ import annotations

import json
from typing import Any, BinaryIO

from research_kb.errors import (
    INPUT_TOO_LARGE,
    SCHEMA_VALIDATION_FAILED,
    Diagnostic,
    ResearchKBError,
)


def read_bounded_json_object(
    stream: BinaryIO,
    *,
    limit: int,
    record_kind: str,
) -> dict[str, Any]:
    content = stream.read(limit + 1)
    if len(content) > limit:
        raise ResearchKBError(
            Diagnostic(INPUT_TOO_LARGE, record_kind, None, "", "stdin input exceeds the allowed byte limit")
        )
    try:
        text = content.decode("utf-8", errors="strict")
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _invalid_input(record_kind) from error
    if not isinstance(value, dict):
        raise _invalid_input(record_kind)
    return value


def _invalid_input(record_kind: str) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            SCHEMA_VALIDATION_FAILED,
            record_kind,
            None,
            "",
            "stdin must contain one UTF-8 JSON object",
        )
    )


__all__ = ["read_bounded_json_object"]
