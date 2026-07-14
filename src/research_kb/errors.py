from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    record_kind: str
    record_id: str | None
    json_path: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
