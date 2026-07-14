from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_jsonl
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
