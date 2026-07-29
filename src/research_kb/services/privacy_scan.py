from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.privacy import PrivacyFinding, scan_repository


@dataclass(frozen=True, slots=True)
class PrivacyScanCommandResult:
    expected_findings: int
    unexpected_findings: tuple[PrivacyFinding, ...]
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "success" if not self.unexpected_findings else "failure",
            "expected_findings": self.expected_findings,
            "unexpected_findings": [
                {
                    "path": item.path,
                    "finding_type": item.finding_type,
                    "detail": item.detail,
                }
                for item in self.unexpected_findings
            ],
        }


class PrivacyScanService:
    def scan(self, *, root: Path, allowlist: Path | None = None) -> PrivacyScanCommandResult:
        result = scan_repository(root, allowlist)
        return PrivacyScanCommandResult(
            len(result.expected),
            result.unexpected,
            0 if result.ok else 1,
        )
