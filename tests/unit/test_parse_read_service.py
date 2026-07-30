from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.parse.synthetic_text import SyntheticTextAdapter
from research_kb.services.parse import ParseService
from research_kb.services.parse_read import ParseReadService
from research_kb.services.registry import RegistryService
from research_kb.storage.json_io import file_sha256
from tests.runtime_helpers import make_runtime_workspace


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _parsed_paper(tmp_path: Path):
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "read-pages.txt"
    source.write_text("Invented page one.\fInvented page two.", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    pages, transaction = ParseService(layout).run(
        paper_id=paper["paper_id"],
        adapter=SyntheticTextAdapter(),
    )
    return layout, source, paper, pages, transaction


def test_parse_read_returns_all_or_one_page_without_mutation(tmp_path: Path) -> None:
    layout, source, paper, pages, transaction = _parsed_paper(tmp_path)
    knowledge_before = _tree_snapshot(layout.knowledge_root)
    source_before = source.read_bytes()
    service = ParseReadService(layout)

    full = service.show(paper_id=paper["paper_id"])
    selected = service.show(paper_id=paper["paper_id"], page="2")

    assert full == {
        "status": "success",
        "interface_version": "1.0",
        "paper_id": paper["paper_id"],
        "parse_run_id": transaction.event_id,
        "parser": {"adapter": "synthetic-text", "version": "1.0"},
        "page_count": 2,
        "returned_page_count": 2,
        "pages": pages,
    }
    assert selected["page_count"] == 2
    assert selected["returned_page_count"] == 1
    assert [item["pdf_page"] for item in selected["pages"]] == [2]
    assert "source_ref" not in str(full)
    assert source.read_bytes() == source_before
    assert _tree_snapshot(layout.knowledge_root) == knowledge_before


@pytest.mark.parametrize("page", ["0", "-1", "not-a-page"])
def test_parse_read_rejects_non_positive_page(page: str, tmp_path: Path) -> None:
    layout, _, paper, _, _ = _parsed_paper(tmp_path)

    with pytest.raises(ResearchKBError) as caught:
        ParseReadService(layout).show(paper_id=paper["paper_id"], page=page)

    assert caught.value.diagnostic.code == "RKBC-002"


def test_parse_read_rejects_unknown_paper_absent_parse_and_missing_page(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "registered-only.txt"
    source.write_text("Invented registered source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={},
    )
    service = ParseReadService(layout)

    for paper_id, page in (
        ("paper_b2222222-2222-4222-8222-222222222222", None),
        (paper["paper_id"], None),
    ):
        with pytest.raises(ResearchKBError) as caught:
            service.show(paper_id=paper_id, page=page)
        assert caught.value.diagnostic.code == "RKBC-005"

    ParseService(layout).run(paper_id=paper["paper_id"], adapter=SyntheticTextAdapter())
    with pytest.raises(ResearchKBError) as caught:
        service.show(paper_id=paper["paper_id"], page="2")
    assert caught.value.diagnostic.code == "RKBC-005"


def test_parse_read_rejects_stale_or_changing_source(tmp_path: Path, monkeypatch) -> None:
    layout, source, paper, _, _ = _parsed_paper(tmp_path)
    source.write_text("Changed source.\n", encoding="utf-8", newline="\n")

    with pytest.raises(ResearchKBError) as caught:
        ParseReadService(layout).show(paper_id=paper["paper_id"])
    assert caught.value.diagnostic.code == "RKBC-009"

    source.write_text("Invented page one.\fInvented page two.", encoding="utf-8", newline="\n")
    expected = paper["source_fingerprint"]["value"]
    calls = iter((expected, "f" * 64))
    monkeypatch.setattr("research_kb.source_resolution.file_sha256", lambda _: next(calls))
    with pytest.raises(ResearchKBError) as caught:
        ParseReadService(layout).show(paper_id=paper["paper_id"])
    assert caught.value.diagnostic.code == "RKBC-009"
    assert file_sha256(source) == expected
