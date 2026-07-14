from pathlib import Path

import pytest

from research_kb.guardian import GuardianService, status_for_findings
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_jsonl
from research_kb.storage.transactions import TransactionManager
from tests.runtime_helpers import make_runtime_workspace


def _register_source(layout) -> tuple[dict, Path]:
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented guardian source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(root_id="alpha-sources", relative_path="study.txt", metadata={})
    return paper, source


def _tree_hashes(root: Path) -> dict[str, str | None]:
    return {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_guardian_check_only_is_read_only_and_succeeds(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _register_source(layout)
    before = _tree_hashes(layout.knowledge_root)

    result = GuardianService(layout).check()

    assert result.report["status"] == "success"
    assert result.report["findings"] == []
    assert result.transaction is None
    assert _tree_hashes(layout.knowledge_root) == before


def test_guardian_detects_changed_source_fingerprint(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    paper, source = _register_source(layout)
    source.write_text("Changed invented source.\n", encoding="utf-8", newline="\n")

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    finding = next(item for item in result.report["findings"] if item["code"] == "RKBC-009")
    assert finding["record_ref"] == paper["paper_id"]


def test_guardian_detects_incomplete_transaction(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    manager = TransactionManager(layout)

    def interrupt(phase: str) -> None:
        if phase == "prepared":
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        manager.promote_bytes(
            target=layout.review_queue_path,
            content=b"",
            target_store="review_queue",
            operation="synthetic_interrupt",
            actor="cli",
            input_refs=[],
            output_refs=[],
            phase_hook=interrupt,
        )

    result = GuardianService(layout).check()

    assert result.report["status"] == "failure"
    assert "RKBC-018" in {item["code"] for item in result.report["findings"]}


def test_guardian_writes_report_only_when_requested(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    _register_source(layout)

    result = GuardianService(layout).check(write_report=True)

    reports = read_jsonl(layout.guardian_reports_path, record_kind="guardian-report", id_field="guardian_report_id")
    assert reports == [result.report]
    assert result.transaction is not None
    assert result.transaction.event_id in {
        item["event_id"] for item in read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    }


def test_guardian_status_is_derived_from_finding_severity() -> None:
    assert status_for_findings([]) == "success"
    assert status_for_findings([{"severity": "warning"}]) == "warning"
    assert status_for_findings([{"severity": "warning"}, {"severity": "error"}]) == "failure"
