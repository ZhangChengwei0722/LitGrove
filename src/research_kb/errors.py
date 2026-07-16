from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


UNSUPPORTED_VERSION = "RKBC-001"
SCHEMA_VALIDATION_FAILED = "RKBC-002"
UNKNOWN_SCHEMA_KIND = "RKBC-003"
DUPLICATE_ID = "RKBC-004"
UNRESOLVED_REFERENCE = "RKBC-005"
INVALID_AUTHORITY = "RKBC-006"
PATH_ESCAPE = "RKBC-007"
NON_POSIX_PATH = "RKBC-008"
GROUNDING_MISMATCH = "RKBC-009"
QUEUE_AS_EVIDENCE = "RKBC-010"
STEP7_BOUNDARY = "RKBC-011"
PRIVACY_LEAK = "RKBC-012"
DUPLICATE_PAPER_CARD = "RKBC-013"
SNAPSHOT_MISMATCH = "RKBC-014"
JSONL_FORMAT_ERROR = "RKBC-015"
LOCK_TIMEOUT = "RKBC-016"
WRITE_CONFLICT = "RKBC-017"
INCOMPLETE_TRANSACTION = "RKBC-018"
WORKSPACE_NOT_INITIALIZED = "RKBC-019"
WORKSPACE_IDENTITY_CONFLICT = "RKBC-020"
WORKSPACE_LAYOUT_CONFLICT = "RKBC-021"
UNSAFE_DIRECTORY_MODE = "RKBC-022"
WORKSPACE_PATH_WARNING = "RKBC-023"
COMPATIBILITY_ADAPTER_ERROR = "RKBC-024"
COMPATIBILITY_OUTPUT_INVALID = "RKBC-025"
PROTECTED_INPUT_CHANGED = "RKBC-026"
WORKSPACE_LAYOUT_UPGRADE_REQUIRED = "RKBC-027"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    record_kind: str
    record_id: str | None
    json_path: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["message"] = redact_absolute_paths(value["message"])
        return value


class ResearchKBError(ValueError):
    def __init__(self, diagnostic: Diagnostic):
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def json_pointer(parts: Iterable[object]) -> str:
    escaped = []
    for part in parts:
        value = str(part).replace("~", "~0").replace("/", "~1")
        escaped.append(value)
    return "/" + "/".join(escaped) if escaped else ""


_QUOTED_VALUE = re.compile(r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)")
_UNQUOTED_WINDOWS_PATH = re.compile(r"(?i)(?<![a-z0-9_])(?:[a-z]:[\\/]|\\\\)[^\s'\",;)\]]+")


def redact_absolute_paths(message: str) -> str:
    def redact_quoted(match: re.Match[str]) -> str:
        value = match.group("value")
        if _looks_like_absolute_path(value):
            return f"{match.group('quote')}<redacted-path>{match.group('quote')}"
        return match.group(0)

    redacted = _QUOTED_VALUE.sub(redact_quoted, message)
    return _UNQUOTED_WINDOWS_PATH.sub("<redacted-path>", redacted)


def _looks_like_absolute_path(value: str) -> bool:
    return bool(
        re.match(r"(?i)^[a-z]:[\\/]", value)
        or value.startswith(("/", "\\\\", "//", "~/"))
    )
