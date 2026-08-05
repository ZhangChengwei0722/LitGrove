import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import FAST_UNIT_FILES, SERIAL_FILES
from tools.validation import load_manifest, marker_for, selectors_for, verify_manifest


def test_shard_manifest_exhaustively_and_uniquely_covers_test_files() -> None:
    report = verify_manifest()
    assert len(report.l3_files) >= 100
    assert report.scale_files == (
        "tests/benchmark/test_p11_operational_density.py",
        "tests/benchmark/test_p2_catalog_scale.py",
    )
    assert set(report.shard_file_counts) == set(report.l3_shards) | {report.scale_shard}


def test_manifest_preserves_accepted_shard_families_and_separate_scale() -> None:
    manifest = load_manifest()
    assert manifest["l3_shards"] == [
        "contract",
        "storage-recovery-a",
        "storage-recovery-b1",
        "storage-recovery-b2",
        "application-semantic-a1",
        "application-semantic-a2",
        "application-semantic-a3a-source",
        "application-semantic-a3a-projection",
        "application-semantic-a3b",
        "application-semantic-a4",
        "application-semantic-b1a-route",
        "application-semantic-b1a-primary",
        "application-semantic-b1a-review",
        "application-semantic-b1b-knowledge",
        "application-semantic-b1b-organization",
        "application-semantic-b1b-screening",
        "application-semantic-b1c",
        "application-semantic-b2",
        "discovery-views-exchange-a",
        "discovery-views-exchange-b",
        "discovery-views-exchange-c",
        "privacy-platform",
        "integration",
        "serial",
    ]
    assert manifest["scale_shard"] == "scale"
    assert selectors_for("scale") == ["tests/benchmark"]
    assert {
        shard: marker_for(shard)
        for shard in (
            "application-semantic-a3a-source",
            "application-semantic-a3a-projection",
            "application-semantic-b1a-route",
            "application-semantic-b1a-primary",
            "application-semantic-b1a-review",
        )
    } == {
        "application-semantic-a3a-source": "reading_source",
        "application-semantic-a3a-projection": "reading_projection",
        "application-semantic-b1a-route": "agent_route",
        "application-semantic-b1a-primary": "agent_primary",
        "application-semantic-b1a-review": "agent_review",
    }


def test_l2_fast_unit_allowlist_is_explicit_workspace_free_and_nonserial() -> None:
    assert FAST_UNIT_FILES.isdisjoint(SERIAL_FILES)
    for relative in FAST_UNIT_FILES:
        path = Path(relative)
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert "tmp_path" not in content
        assert "tmpdir" not in content


def test_validation_runner_records_timeout_separately_from_failure(tmp_path: Path) -> None:
    receipt = tmp_path / "timeout.json"
    result = subprocess.run(
        [
            sys.executable,
            "tools/run_validation.py",
            "--level",
            "L1",
            "--selector",
            "tests/unit/test_versions.py",
            "--timeout-seconds",
            "0.001",
            "--receipt",
            str(receipt),
        ],
        check=False,
    )

    assert result.returncode == 124
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["status"] == "timed_out"
    assert payload["return_code"] == 124
    assert payload["commands"][0]["timed_out"] is True
    assert payload["commands"][0]["timeout_seconds"] == 0.001
