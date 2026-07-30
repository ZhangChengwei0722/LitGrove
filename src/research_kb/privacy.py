from __future__ import annotations

import json
import re
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from research_kb.errors import PRIVACY_LEAK, Diagnostic


SKIP_PARTS = {".git", ".venv", ".wheel-smoke", "__pycache__", ".pytest_cache"}


@dataclass(frozen=True, slots=True)
class PrivacyFinding:
    path: str
    finding_type: str
    detail: str

    def diagnostic(self) -> Diagnostic:
        return Diagnostic(PRIVACY_LEAK, "privacy-scan", None, self.path, self.detail)


@dataclass(frozen=True, slots=True)
class PrivacyScanResult:
    expected: tuple[PrivacyFinding, ...]
    unexpected: tuple[PrivacyFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.unexpected


def scan_repository(root: Path, allowlist_path: Path | None = None) -> PrivacyScanResult:
    resolved_root = root.resolve()
    if allowlist_path is None:
        default = resolved_root / "tests" / "fixtures" / "privacy_allowlist.json"
        allowlist_path = default if default.is_file() else None
    allowlist = _load_allowlist(allowlist_path, resolved_root)
    findings: list[PrivacyFinding] = []
    for path in sorted(resolved_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.relative_to(resolved_root).parts):
            continue
        relative = path.relative_to(resolved_root).as_posix()
        if path.suffix.lower() in {".whl", ".zip"}:
            findings.extend(_scan_zip(path, relative))
        elif path.name.lower().endswith((".tar.gz", ".tgz")):
            findings.extend(_scan_tar(path, relative))
        else:
            findings.extend(_scan_bytes(path.read_bytes(), relative))
    expected: list[PrivacyFinding] = []
    unexpected: list[PrivacyFinding] = []
    actual_counts: Counter[tuple[str, str]] = Counter((item.path, item.finding_type) for item in findings)
    for finding in findings:
        key = (finding.path, finding.finding_type)
        if key in allowlist and actual_counts[key] == allowlist[key]:
            expected.append(finding)
        else:
            unexpected.append(finding)
    for key, count in allowlist.items():
        if actual_counts[key] != count:
            unexpected.append(
                PrivacyFinding(key[0], "allowlist_mismatch", f"expected {count} {key[1]} findings, got {actual_counts[key]}")
            )
    return PrivacyScanResult(tuple(expected), tuple(unexpected))


def _load_allowlist(path: Path | None, root: Path) -> dict[tuple[str, str], int]:
    if path is None:
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    result: dict[tuple[str, str], int] = {}
    for entry in loaded.get("entries", []):
        relative = Path(entry["path"]).as_posix()
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("privacy allowlist paths must be repository-relative")
        for finding_type, count in entry["expected"].items():
            result[(relative, finding_type)] = int(count)
    return result


def _scan_zip(path: Path, relative: str) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                findings.extend(_scan_bytes(archive.read(member), f"{relative}!{member.filename}"))
    except zipfile.BadZipFile:
        findings.append(PrivacyFinding(relative, "invalid_archive", "archive cannot be inspected"))
    return findings


def _scan_tar(path: Path, relative: str) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is not None:
                    findings.extend(_scan_bytes(extracted.read(), f"{relative}!{member.name}"))
    except tarfile.TarError:
        findings.append(PrivacyFinding(relative, "invalid_archive", "archive cannot be inspected"))
    return findings


def _scan_bytes(content: bytes, path: str) -> list[PrivacyFinding]:
    findings: list[PrivacyFinding] = []
    pdf_signature = bytes((37,)) + b"PDF-"
    for _ in range(content.count(pdf_signature)):
        findings.append(PrivacyFinding(path, "pdf_signature", "PDF binary signature outside an approved synthetic asset"))
    text = content.decode("utf-8", errors="ignore")
    slash = chr(47)
    backslash = chr(92)
    drive_pattern = re.compile(r"(?i)(?:^|[\s\"'])([a-z]:[\\/])")
    for _ in drive_pattern.finditer(text):
        findings.append(PrivacyFinding(path, "windows_absolute_path", "Windows absolute path detected"))
    unc_pattern = re.compile(
        r"(?:^|[\s\"'])" + re.escape(backslash * 2) + r"[A-Za-z0-9._-]+" + re.escape(backslash)
    )
    for _ in unc_pattern.finditer(text):
        findings.append(PrivacyFinding(path, "unc_path", "UNC-shaped path detected"))
    home_count = text.count(slash + "Users" + slash) + text.count(slash + "home" + slash)
    for _ in range(home_count):
        findings.append(PrivacyFinding(path, "posix_home_path", "user-home-shaped path detected"))
    credential_count = sum(
        1
        for _ in re.finditer(
            r"(?<![A-Za-z0-9])" + re.escape("sk" + "-") + r"[A-Za-z0-9_-]{8,}",
            text,
        )
    )
    credential_count += sum(1 for _ in re.finditer(r"(?i)(?:token|password|secret)\s*[=:]\s*[^\s\"']{8,}", text))
    for _ in range(credential_count):
        findings.append(PrivacyFinding(path, "credential_like", "credential-like value detected"))
    markers = (("Q" + "001"), ("T" + "PD"))
    marker_count = sum(text.count(marker) for marker in markers)
    for _ in range(marker_count):
        findings.append(PrivacyFinding(path, "private_marker", "private-domain marker detected"))
    return findings
