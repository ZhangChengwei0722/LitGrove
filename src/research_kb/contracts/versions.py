from __future__ import annotations

import re
from dataclasses import dataclass

from research_kb.errors import UNSUPPORTED_VERSION, Diagnostic, ResearchKBError


SUPPORTED_VERSION = "1.0"
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True, order=True)
class ContractVersion:
    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


def parse_version(value: object) -> ContractVersion:
    if not isinstance(value, str):
        raise _unsupported(value)
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise _unsupported(value)
    return ContractVersion(int(match.group(1)), int(match.group(2)))


def require_supported(value: object) -> ContractVersion:
    parsed = parse_version(value)
    if str(parsed) != SUPPORTED_VERSION:
        raise _unsupported(value)
    return parsed


def _unsupported(value: object) -> ResearchKBError:
    return ResearchKBError(
        Diagnostic(
            code=UNSUPPORTED_VERSION,
            record_kind="contract-version",
            record_id=None,
            json_path="/schema_version",
            message=f"unsupported contract/schema version: {value!r}",
        )
    )
