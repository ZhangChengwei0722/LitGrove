from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl
from tests.runtime_helpers import make_runtime_workspace


def test_registry_add_hashes_source_and_links_exact_duplicates(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first_source = layout.source_roots["alpha-sources"] / "study-one.txt"
    second_source = layout.source_roots["alpha-sources"] / "study-copy.txt"
    content = "Invented chamber response.\n"
    first_source.write_text(content, encoding="utf-8", newline="\n")
    second_source.write_text(content, encoding="utf-8", newline="\n")
    before = {path: file_sha256(path) for path in (first_source, second_source)}
    service = RegistryService(layout)

    first, _ = service.add(
        root_id="alpha-sources",
        relative_path="study-one.txt",
        metadata={"bibliography": {"title": "Invented chamber study"}},
    )
    second, _ = service.add(
        root_id="alpha-sources",
        relative_path="study-copy.txt",
        metadata={"bibliography": {"title": "Invented duplicate record"}},
    )

    stored = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    by_id = {record["paper_id"]: record for record in stored}
    assert by_id[first["paper_id"]]["duplicate_candidate_ids"] == [second["paper_id"]]
    assert by_id[second["paper_id"]]["duplicate_candidate_ids"] == [first["paper_id"]]
    assert all(record["screening_status"] == "candidate" for record in stored)
    assert all(record["automation_status"] == "passed_auto_checks" for record in stored)
    assert {path: file_sha256(path) for path in before} == before


def test_registry_add_preserves_distinct_sources(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first_source = layout.source_roots["alpha-sources"] / "study-one.txt"
    second_source = layout.source_roots["alpha-sources"] / "study-two.txt"
    first_source.write_text("Invented response A.\n", encoding="utf-8", newline="\n")
    second_source.write_text("Invented response B.\n", encoding="utf-8", newline="\n")
    service = RegistryService(layout)

    first, _ = service.add(root_id="alpha-sources", relative_path="study-one.txt", metadata={})
    second, _ = service.add(root_id="alpha-sources", relative_path="study-two.txt", metadata={})

    assert first["source_fingerprint"] != second["source_fingerprint"]
    assert first["duplicate_candidate_ids"] == []
    assert second["duplicate_candidate_ids"] == []


def test_registry_replace_preserves_source_identity_and_guards_final_screening(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented registry source.\n", encoding="utf-8", newline="\n")
    service = RegistryService(layout)
    paper, _ = service.add(root_id="alpha-sources", relative_path="study.txt", metadata={})

    replaced, _ = service.replace(
        paper_id=paper["paper_id"],
        changes={"bibliography": {"title": "Updated invented title"}},
        actor="agent",
    )

    assert replaced["paper_id"] == paper["paper_id"]
    assert replaced["source_ref"] == paper["source_ref"]
    assert replaced["source_fingerprint"] == paper["source_fingerprint"]
    assert replaced["bibliography"]["title"] == "Updated invented title"
    with pytest.raises(ResearchKBError) as caught:
        service.replace(
            paper_id=paper["paper_id"],
            changes={"screening_status": "included"},
            actor="agent",
        )
    assert caught.value.diagnostic.code == "RKBC-006"


def test_registry_rejects_non_object_bibliography(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented source.\n", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError) as caught:
        RegistryService(layout).add(
            root_id="alpha-sources",
            relative_path="study.txt",
            metadata={"bibliography": []},
        )

    assert caught.value.diagnostic.code == "RKBC-002"


def test_registry_source_change_at_commit_requires_manual_resolution(tmp_path: Path, monkeypatch) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented stable source.\n", encoding="utf-8", newline="\n")
    from research_kb.storage import transactions

    original_replace = transactions.replace_temp

    def replace_and_change_source(temporary: Path, target: Path) -> None:
        original_replace(temporary, target)
        source.write_text("Invented changed source.\n", encoding="utf-8", newline="\n")

    monkeypatch.setattr(transactions, "replace_temp", replace_and_change_source)

    with pytest.raises(ResearchKBError) as caught:
        RegistryService(layout).add(root_id="alpha-sources", relative_path="study.txt", metadata={})

    assert caught.value.diagnostic.code == "RKBC-018"
    assert read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id") == []
    journals = list(layout.transactions_root.glob("*.json"))
    assert len(journals) == 1
    journal = read_json_document(journals[0], record_kind="transaction-journal")
    assert journal["phase"] == "needs_resolution"
    assert journal["result"] == "needs_resolution"


def test_registry_replace_source_change_at_commit_requires_manual_resolution(tmp_path: Path, monkeypatch) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented stable source.\n", encoding="utf-8", newline="\n")
    service = RegistryService(layout)
    paper, _ = service.add(root_id="alpha-sources", relative_path="study.txt", metadata={})
    from research_kb.storage import transactions

    original_replace = transactions.replace_temp

    def replace_and_change_source(temporary: Path, target: Path) -> None:
        original_replace(temporary, target)
        source.write_text("Invented changed source.\n", encoding="utf-8", newline="\n")

    monkeypatch.setattr(transactions, "replace_temp", replace_and_change_source)

    with pytest.raises(ResearchKBError) as caught:
        service.replace(
            paper_id=paper["paper_id"],
            changes={"bibliography": {"title": "Invented replacement title"}},
        )

    assert caught.value.diagnostic.code == "RKBC-018"
    journals = [
        read_json_document(path, record_kind="transaction-journal")
        for path in layout.transactions_root.glob("*.json")
    ]
    replace_journal = next(item for item in journals if item["operation"] == "registry_replace")
    assert replace_journal["phase"] == "needs_resolution"
    assert replace_journal["result"] == "needs_resolution"
    events = read_jsonl(layout.process_events_path, record_kind="process-event", id_field="event_id")
    assert replace_journal["event_id"] not in {item["event_id"] for item in events}
