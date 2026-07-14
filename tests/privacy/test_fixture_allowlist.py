import json
from pathlib import Path

from research_kb.privacy import scan_repository


ROOT = Path(__file__).resolve().parents[2]


def test_privacy_allowlist_is_exact_file_and_exact_counts() -> None:
    path = ROOT / "tests" / "fixtures" / "privacy_allowlist.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == {
        "entries": [
            {
                "path": "tests/fixtures/invalid/privacy_cases.txt",
                "expected": {
                    "windows_absolute_path": 1,
                    "unc_path": 1,
                    "posix_home_path": 1,
                    "credential_like": 1,
                    "private_marker": 2,
                    "pdf_signature": 1,
                },
            }
        ]
    }


def test_wrong_allowlist_count_is_an_unexpected_finding(tmp_path: Path) -> None:
    allowlist = tmp_path / "allowlist.json"
    allowlist.write_text(
        json.dumps({"entries": [{"path": "tests/fixtures/invalid/privacy_cases.txt", "expected": {"private_marker": 1}}]}),
        encoding="utf-8",
    )
    result = scan_repository(ROOT, allowlist)
    assert not result.ok
    assert any(item.finding_type == "allowlist_mismatch" for item in result.unexpected)
