from pathlib import Path

import pytest

from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.process_events import read_process_events
from research_kb.errors import ResearchKBError
from research_kb.services.parse import ParseService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256, read_json_document, read_jsonl
from tests.runtime_helpers import make_runtime_workspace


def test_parse_service_promotes_pages_and_preserves_source(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented first page.\fInvented second page.", encoding="utf-8", newline="\n")
    before = file_sha256(source)
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path="study.txt",
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )

    pages, result = ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())

    stored = read_jsonl(layout.parse_path(paper["paper_id"]), record_kind="parsed-page")
    assert [item["pdf_page"] for item in stored] == [1, 2]
    assert {item["parse_run_id"] for item in stored} == {result.event_id}
    assert pages == stored
    assert file_sha256(source) == before


def test_parse_failure_records_failure_event_without_replacing_output(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(root_id="alpha-sources", relative_path="study.txt", metadata={})
    target = layout.parse_path(paper["paper_id"])
    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    before = target.read_bytes()

    class FailingAdapter:
        name = "synthetic-failure"
        version = "1.0"

        def parse(
            self,
            source_path: Path,
            *,
            paper_id: str,
            parse_run_id: str,
        ) -> list[dict[str, object]]:
            raise ValueError("invented adapter failure")

    with pytest.raises(ValueError, match="invented adapter failure"):
        ParseService(layout).run(paper_id=paper["paper_id"], adapter=FailingAdapter())

    assert target.read_bytes() == before
    assert read_process_events(layout.process_events_path)[-1]["result"] == "failure"


def test_parse_source_change_at_commit_requires_manual_resolution(tmp_path: Path, monkeypatch) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "study.txt"
    source.write_text("Invented stable source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(root_id="alpha-sources", relative_path="study.txt", metadata={})
    existing_events = read_process_events(layout.process_events_path)
    from research_kb.storage import transactions

    original_replace = transactions.replace_temp

    def replace_and_change_source(temporary: Path, target: Path) -> None:
        original_replace(temporary, target)
        if target == layout.parse_path(paper["paper_id"]).resolve():
            source.write_text("Invented changed source.\n", encoding="utf-8", newline="\n")

    monkeypatch.setattr(transactions, "replace_temp", replace_and_change_source)

    with pytest.raises(ResearchKBError) as caught:
        ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())

    assert caught.value.diagnostic.code == "RKBC-018"
    assert read_process_events(layout.process_events_path) == existing_events
    journals = [
        read_json_document(path, record_kind="transaction-journal")
        for path in layout.transactions_root.glob("*.json")
    ]
    parse_journal = next(item for item in journals if item["operation"] == "parse_run")
    assert parse_journal["phase"] == "needs_resolution"
    assert parse_journal["result"] == "needs_resolution"
