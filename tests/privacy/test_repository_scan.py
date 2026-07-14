from pathlib import Path

from research_kb.privacy import scan_repository


ROOT = Path(__file__).resolve().parents[2]


def test_repository_scan_has_only_exact_allowlisted_findings() -> None:
    result = scan_repository(ROOT)
    assert result.ok, result.unexpected
    assert len(result.expected) == 7


def test_scan_without_allowlist_reports_intentional_negative_fixture() -> None:
    result = scan_repository(ROOT, ROOT / "tests" / "fixtures" / "empty_allowlist.json")
    finding_types = {item.finding_type for item in result.unexpected}
    assert {"windows_absolute_path", "unc_path", "posix_home_path", "credential_like", "private_marker", "pdf_signature"} <= finding_types
