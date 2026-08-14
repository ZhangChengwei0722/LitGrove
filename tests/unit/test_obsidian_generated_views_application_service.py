from __future__ import annotations

import json
import shutil
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research_kb.bundle import load_workspace_entries
from research_kb.cli import main
from research_kb.errors import SNAPSHOT_MISMATCH, ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.obsidian_views import project_obsidian_views
from research_kb.services import (
    CatalogProjectionService,
    ObsidianGeneratedViewsApplicationService,
    ResearchOrganizationService,
    WorkspaceSessionService,
)
from research_kb.services.records import RecordService
from research_kb.storage.json_io import (
    read_json_document,
    read_jsonl,
    serialize_json,
    serialize_jsonl,
    sha256_bytes,
)
from tests.unit.test_review_memory_service import prepare_review_paper, review_request
from tests.unit.test_step7_candidate_service import _seed_workspace


FIXED_TIME = datetime(2026, 8, 4, 4, 0, 0, tzinfo=timezone.utc)
APPROVAL = {
    "receipt_id": "p9-synthetic-direction",
    "approved_by": "user",
    "approved_at": "2026-08-04T04:00:00Z",
    "origin": "user_authored",
}


def _workspace(tmp_path: Path):
    layout, by_kind = _seed_workspace(tmp_path)
    for kind in ("step7-insight", "step7-cross-view"):
        layout.step7_store_path(kind).write_bytes(serialize_jsonl(by_kind[kind]))
    review_paper, _ = prepare_review_paper(layout)
    review, _ = RecordService(layout).promote(
        review_request(review_paper["paper_id"]),
        actor="agent",
    )
    by_kind["registry-paper"].append(review_paper)
    by_kind["review-memory"] = [review]
    direction, _ = ResearchOrganizationService(layout).promote_direction(
        {
            "name": "Synthetic direction",
            "scope": "A fabricated direction for generated-view testing.",
            "status": "active",
            "unit_links": [],
            "gap_notes": ["Synthetic coverage gap."],
        },
        approval=APPROVAL,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    service = ObsidianGeneratedViewsApplicationService(clock=lambda: FIXED_TIME)
    return layout, session, service, by_kind, direction["direction_id"]


def _render(service, session, *, tables=("library_summary",), discard=False):
    preview = service.preview_render(session, optional_tables=list(tables))
    return service.render(
        session,
        {
            "optional_tables": list(tables),
            "expected_state": preview["expected_state"],
            "discard_managed_edits": discard,
        },
        actor="user",
    )


def _entry(status: dict, *, view_kind: str, view_id: str) -> dict:
    return next(
        item
        for item in status["entries"]
        if item["view_kind"] == view_kind and item["view_id"] == view_id
    )


def _draft(drafts, *, view_kind: str, view_id: str):
    return next(
        item
        for item in drafts
        if item.view_kind == view_kind and item.view_id == view_id
    )


def test_initial_render_is_complete_traceable_and_idempotent(tmp_path: Path) -> None:
    layout, session, service, by_kind, direction_id = _workspace(tmp_path)

    preview = service.preview_render(session, optional_tables=["library_summary"])
    assert preview["projection_state"] == "missing"
    assert preview["changed_file_count"] > 0
    rendered = _render(service, session)
    status = service.status(session)

    assert rendered["result"] == "committed"
    assert rendered["canonical_scientific_write"] is False
    assert rendered["persistent_writes"] == 1
    assert status["projection_state"] == "ready"
    assert status["stale_count"] == 0
    assert {item["freshness"] for item in status["entries"]} == {"current"}

    logical_paths = {item["logical_path"] for item in status["entries"]}
    review = by_kind["review-memory"][0]
    question_id = by_kind["question-mapping"][0]["question_id"]
    assert {
        "Home.md",
        "Papers/_index.md",
        "Reviews/_index.md",
        f"Reviews/{review['paper_id']}.md",
        f"Directions/{direction_id}.md",
        f"Questions/{question_id}.md",
        f"Research Synthesis/{question_id}.md",
        "Tables/library_summary.md",
    } <= logical_paths

    generation = layout.obsidian_generation_path(status["generation_id"])
    paper_id = by_kind["paper-card"][0]["paper_id"]
    paper_note = (generation / "Papers" / f"{paper_id}.md").read_text(encoding="utf-8")
    review_note = (generation / "Reviews" / f"{review['paper_id']}.md").read_text(encoding="utf-8")
    synthesis_note = (generation / "Research Synthesis" / f"{question_id}.md").read_text(encoding="utf-8")
    assert "Canonical Evidence" in paper_note
    assert "PDF Page" in paper_note and "Locator" in paper_note
    assert "background_only: true" in review_note
    assert "Review Background" in synthesis_note
    assert by_kind["registry-paper"][0]["source_ref"]["relative_path"] not in paper_note

    manifest_before = layout.obsidian_manifest_path.read_bytes()
    tree_before = {
        item["logical_path"]: (generation / Path(*item["logical_path"].split("/"))).read_bytes()
        for item in status["entries"]
    }
    assert all(b"\r\n" not in content and content.decode("utf-8") for content in tree_before.values())
    repeated = _render(service, session)
    assert repeated["result"] == "no_change"
    assert repeated["persistent_writes"] == 0
    assert layout.obsidian_manifest_path.read_bytes() == manifest_before
    assert tree_before == {
        item["logical_path"]: (generation / Path(*item["logical_path"].split("/"))).read_bytes()
        for item in service.status(session)["entries"]
    }


def test_only_consuming_views_stale_and_rerender_preserves_unrelated_bytes(tmp_path: Path) -> None:
    layout, session, service, by_kind, _ = _workspace(tmp_path)
    _render(service, session, tables=("library_summary", "question_coverage"))
    before = service.status(session)
    changed_paper_id = by_kind["paper-card"][0]["paper_id"]
    unrelated_paper_id = by_kind["paper-card"][1]["paper_id"]
    unrelated_before = _entry(before, view_kind="paper", view_id=unrelated_paper_id)
    old_generation = layout.obsidian_generation_path(before["generation_id"])
    unrelated_bytes = (old_generation / "Papers" / f"{unrelated_paper_id}.md").read_bytes()

    card_path = layout.paper_card_path(changed_paper_id)
    card = __import__("json").loads(card_path.read_text(encoding="utf-8"))
    card["updated_at"] = "2026-08-04T04:00:01Z"
    card_path.write_bytes(serialize_json(card))

    stale = service.status(session)
    assert _entry(stale, view_kind="paper", view_id=changed_paper_id)["freshness"] == "stale_upstream"
    assert _entry(stale, view_kind="paper", view_id=unrelated_paper_id)["freshness"] == "current"

    _render(service, session, tables=("library_summary", "question_coverage"))
    current = service.status(session)
    new_generation = layout.obsidian_generation_path(current["generation_id"])
    unrelated_after = _entry(current, view_kind="paper", view_id=unrelated_paper_id)
    assert current["stale_count"] == 0
    assert unrelated_after["content_digest"] == unrelated_before["content_digest"]
    assert unrelated_after["rendered_at"] == unrelated_before["rendered_at"]
    assert (new_generation / "Papers" / f"{unrelated_paper_id}.md").read_bytes() == unrelated_bytes


def test_card_metadata_preserves_question_unit_scope_and_research_synthesis_freshness_contract(
    tmp_path: Path,
) -> None:
    layout, session, service, by_kind, _ = _workspace(tmp_path)
    _render(service, session)
    question_ids = [item["question_id"] for item in by_kind["question-mapping"]]

    card_path = layout.paper_card_path(by_kind["paper-card"][0]["paper_id"])
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["updated_at"] = "2026-08-04T04:00:01Z"
    card_path.write_bytes(serialize_json(card))

    status = service.status(session)
    assert all(
        _entry(status, view_kind="question", view_id=question_id)["freshness"] == "current"
        for question_id in question_ids
    )
    assert all(
        _entry(status, view_kind="research_synthesis", view_id=question_id)["freshness"]
        == "stale_upstream"
        for question_id in question_ids
    )


def test_selected_card_unit_change_stales_only_the_consuming_question(tmp_path: Path) -> None:
    layout, session, service, by_kind, _ = _workspace(tmp_path)
    _render(service, session)
    first_question, second_question = by_kind["question-mapping"]
    selected_unit_id = first_question["paper_links"][0]["selected_card_unit_ids"][0]
    paper_id = first_question["paper_links"][0]["paper_id"]

    card_path = layout.paper_card_path(paper_id)
    card = json.loads(card_path.read_text(encoding="utf-8"))
    selected_unit = next(
        unit
        for section in card["sections"]
        for unit in section["units"]
        if unit["unit_id"] == selected_unit_id
    )
    selected_unit["statement"] = "The selected synthetic Unit changed."
    card["updated_at"] = "2026-08-04T04:00:01Z"
    card_path.write_bytes(serialize_json(card))

    status = service.status(session)
    assert _entry(
        status,
        view_kind="question",
        view_id=first_question["question_id"],
    )["freshness"] == "stale_upstream"
    assert _entry(
        status,
        view_kind="question",
        view_id=second_question["question_id"],
    )["freshness"] == "current"


def test_research_synthesis_source_candidate_change_is_question_scoped(tmp_path: Path) -> None:
    layout, session, service, by_kind, _ = _workspace(tmp_path)
    _render(service, session)
    source = deepcopy(by_kind["step7-synthesis"][0])
    source["title"] = "Revised synthetic source candidate"
    source["updated_at"] = "2026-08-04T04:00:01Z"
    layout.step7_store_path("step7-synthesis").write_bytes(serialize_jsonl([source]))

    status = service.status(session)
    source_question_id = source["question_id"]
    other_question_id = by_kind["step7-insight"][0]["question_id"]
    assert _entry(
        status,
        view_kind="research_synthesis",
        view_id=source_question_id,
    )["freshness"] == "stale_upstream"
    assert _entry(
        status,
        view_kind="research_synthesis",
        view_id=other_question_id,
    )["freshness"] == "current"
    assert all(
        _entry(status, view_kind="question", view_id=question_id)["freshness"] == "current"
        for question_id in (source_question_id, other_question_id)
    )


def test_research_synthesis_review_unit_dependency_is_exact(tmp_path: Path) -> None:
    layout, _, _, _, _ = _workspace(tmp_path)
    entries = load_workspace_entries(layout)
    review = next(record for kind, record in entries if kind == "review-memory")
    entries = [(kind, record) for kind, record in entries if kind != "review-memory"]
    selected_unit = next(
        unit
        for section in review["sections"]
        for unit in section["units"]
    )
    unrelated_unit = deepcopy(selected_unit)
    unrelated_unit["review_unit_id"] = "reviewunit_99999999-9999-4999-8999-999999999999"
    unrelated_unit["content"] = "Unrelated synthetic review background."
    review["sections"][0]["units"].append(unrelated_unit)
    review_revision_id = "reviewrev_99999999-9999-4999-8999-999999999999"
    entries.append(
        (
            "review-semantic-bundle",
            {
                "paper_id": review["paper_id"],
                "active_revision_id": review_revision_id,
                "revisions": [
                    {"revision_id": review_revision_id, "review_memory": review}
                ],
            },
        )
    )

    candidate = next(
        record for kind, record in entries if kind == "step7-synthesis"
    )
    mapping = next(
        record
        for kind, record in entries
        if kind == "question-mapping" and record["question_id"] == candidate["question_id"]
    )
    entries = [
        (kind, record)
        for kind, record in entries
        if not (kind == "question-mapping" and record["question_id"] == mapping["question_id"])
    ]
    question_revision_id = "questionrev_99999999-9999-4999-8999-999999999999"
    background_id = "qbackground_99999999-9999-4999-8999-999999999999"
    entries.append(
        (
            "question-revision-bundle",
            {
                "question_id": mapping["question_id"],
                "active_revision_id": question_revision_id,
                "revisions": [
                    {
                        "revision_id": question_revision_id,
                        "question_mapping": mapping,
                        "background_links": [
                            {
                                "question_background_id": background_id,
                                "link": {
                                    "source_kind": "review_unit",
                                    "paper_id": review["paper_id"],
                                    "review_memory_id": review["review_memory_id"],
                                    "source_unit_id": selected_unit["review_unit_id"],
                                    "source_revision_id": review_revision_id,
                                    "role": "question_background",
                                    "evidence_ids": [],
                                },
                            }
                        ],
                    }
                ],
            },
        )
    )
    candidate["review_background_base"] = [
        {
            "paper_id": review["paper_id"],
            "review_memory_id": review["review_memory_id"],
            "review_revision_id": review_revision_id,
            "question_background_ids": [background_id],
            "review_unit_ids": [selected_unit["review_unit_id"]],
        }
    ]
    candidate["input_snapshot"]["review_unit_ids"] = [selected_unit["review_unit_id"]]

    before_synthesis = _draft(
        project_obsidian_views(entries),
        view_kind="research_synthesis",
        view_id=candidate["question_id"],
    )
    selected_change = deepcopy(entries)
    selected_memory = next(
        record for kind, record in selected_change if kind == "review-semantic-bundle"
    )["revisions"][0]["review_memory"]
    next(
        unit
        for section in selected_memory["sections"]
        for unit in section["units"]
        if unit["review_unit_id"] == selected_unit["review_unit_id"]
    )["content"] = "Changed selected review background."
    selected_after = _draft(
        project_obsidian_views(selected_change),
        view_kind="research_synthesis",
        view_id=candidate["question_id"],
    )

    unrelated_change = deepcopy(entries)
    unrelated_memory = next(
        record for kind, record in unrelated_change if kind == "review-semantic-bundle"
    )["revisions"][0]["review_memory"]
    next(
        unit
        for section in unrelated_memory["sections"]
        for unit in section["units"]
        if unit["review_unit_id"] == unrelated_unit["review_unit_id"]
    )["content"] = "Changed unrelated review background."
    unrelated_after = _draft(
        project_obsidian_views(unrelated_change),
        view_kind="research_synthesis",
        view_id=candidate["question_id"],
    )

    assert selected_after.source_watermark != before_synthesis.source_watermark
    assert unrelated_after.source_watermark == before_synthesis.source_watermark


def test_domain_profile_label_and_version_have_distinct_dependency_scope(tmp_path: Path) -> None:
    layout, _, _, by_kind, _ = _workspace(tmp_path)
    entries = load_workspace_entries(layout)
    paper_id = by_kind["paper-card"][0]["paper_id"]
    question_id = by_kind["step7-synthesis"][0]["question_id"]
    before = project_obsidian_views(entries)
    paper_before = _draft(before, view_kind="paper", view_id=paper_id)
    question_before = _draft(before, view_kind="question", view_id=question_id)
    synthesis_before = _draft(before, view_kind="research_synthesis", view_id=question_id)

    label_change = deepcopy(entries)
    profile = next(record for kind, record in label_change if kind == "domain-profile")
    profile["paper_card_sections"][0]["label"] = "Changed synthetic label"
    label_after = project_obsidian_views(label_change)
    assert _draft(label_after, view_kind="paper", view_id=paper_id).source_watermark != (
        paper_before.source_watermark
    )
    assert _draft(label_after, view_kind="question", view_id=question_id).source_watermark == (
        question_before.source_watermark
    )
    assert _draft(
        label_after,
        view_kind="research_synthesis",
        view_id=question_id,
    ).source_watermark == synthesis_before.source_watermark

    version_change = deepcopy(entries)
    profile = next(record for kind, record in version_change if kind == "domain-profile")
    profile["domain_profile"]["version"] = "1.1"
    version_after = project_obsidian_views(version_change)
    assert _draft(
        version_after,
        view_kind="research_synthesis",
        view_id=question_id,
    ).source_watermark != synthesis_before.source_watermark


def test_edited_active_file_blocks_ordinary_render_and_user_discard_restores_it(tmp_path: Path) -> None:
    layout, session, service, by_kind, _ = _workspace(tmp_path)
    _render(service, session)
    status = service.status(session)
    paper_id = by_kind["paper-card"][0]["paper_id"]
    path = layout.obsidian_generation_path(status["generation_id"]) / "Papers" / f"{paper_id}.md"
    expected = path.read_bytes()
    path.write_bytes(expected + b"\nuser edit\n")

    edited = service.status(session)
    assert edited["integrity_state"] == "edited_managed_file"
    assert f"Papers/{paper_id}.md" in edited["edited_paths"]
    preview = service.preview_render(session, optional_tables=["library_summary"])
    with pytest.raises(ResearchKBError, match="edited"):
        service.render(
            session,
            {
                "optional_tables": ["library_summary"],
                "expected_state": preview["expected_state"],
                "discard_managed_edits": False,
            },
            actor="user",
        )
    with pytest.raises(ResearchKBError):
        service.render(
            session,
            {
                "optional_tables": ["library_summary"],
                "expected_state": preview["expected_state"],
                "discard_managed_edits": True,
            },
            actor="agent",
        )

    repaired = service.render(
        session,
        {
            "optional_tables": ["library_summary"],
            "expected_state": preview["expected_state"],
            "discard_managed_edits": True,
        },
        actor="user",
    )
    repaired_status = service.status(session)
    repaired_path = (
        layout.obsidian_generation_path(repaired_status["generation_id"])
        / "Papers"
        / f"{paper_id}.md"
    )
    assert repaired["result"] == "committed"
    assert repaired_status["integrity_state"] == "intact"
    assert repaired_status["generation_id"] != status["generation_id"]
    assert b"user edit" not in repaired_path.read_bytes()
    assert b"Canonical Evidence" in repaired_path.read_bytes()
    assert path.read_bytes().endswith(b"user edit\n")


def test_projection_deletion_is_fully_rebuildable(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    _render(service, session, tables=("question_coverage",))
    first = service.status(session)
    first_paths = {item["logical_path"] for item in first["entries"]}
    shutil.rmtree(layout.obsidian_views_root)

    assert service.status(session)["projection_state"] == "missing"
    rebuilt = _render(service, session, tables=("question_coverage",))
    assert rebuilt["result"] == "committed"
    assert {item["logical_path"] for item in service.status(session)["entries"]} == first_paths


def test_generated_views_do_not_change_guardian_or_catalog_canonical_projection(
    tmp_path: Path,
) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    guardian_before = GuardianService(layout).check().report["findings"]
    catalog = CatalogProjectionService(session, tmp_path / "app-state")
    catalog_before = catalog.rebuild()

    _render(service, session, tables=("library_summary", "question_coverage"))

    assert GuardianService(layout).check().report["findings"] == guardian_before
    catalog_after = catalog.status()
    assert catalog_after["projection_state"] == "current"
    assert catalog_after["source_watermark"] == catalog_before["source_watermark"]


def test_untrusted_source_text_is_escaped_and_never_becomes_embed_or_path(tmp_path: Path) -> None:
    layout, session, service, by_kind, _ = _workspace(tmp_path)
    papers = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    papers[0]["bibliography"]["title"] = "<script>alert(1)</script> ![[evil]] [x](file:///private)"
    layout.registry_path.write_bytes(serialize_jsonl(papers))

    _render(service, session)
    status = service.status(session)
    note = (
        layout.obsidian_generation_path(status["generation_id"])
        / "Papers"
        / f"{papers[0]['paper_id']}.md"
    ).read_text(encoding="utf-8")

    assert "<script>" not in note
    assert "\\<script\\>" in note
    assert "![[evil]]" not in note
    assert "file:///private" not in note
    assert by_kind["registry-paper"][0]["source_ref"]["relative_path"] not in note


def test_optional_table_removal_refreshes_home_and_preserves_complete_generation(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    _render(service, session, tables=("library_summary", "question_coverage"))
    before = service.status(session)
    before_root = layout.obsidian_generation_path(before["generation_id"])
    assert "Library Summary" in (before_root / "Home.md").read_text(encoding="utf-8")

    changed = _render(service, session, tables=("question_coverage",))
    after = service.status(session)
    after_root = layout.obsidian_generation_path(after["generation_id"])
    paths = {item["logical_path"] for item in after["entries"]}

    assert changed["result"] == "committed"
    assert changed["removed_file_count"] == 1
    assert "Tables/library_summary.md" not in paths
    assert "Tables/question_coverage.md" in paths
    assert "Library Summary" not in (after_root / "Home.md").read_text(encoding="utf-8")
    assert before_root.is_dir()


def test_unknown_managed_file_requires_discard_and_is_not_imported(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    _render(service, session)
    status = service.status(session)
    generation = layout.obsidian_generation_path(status["generation_id"])
    unknown = generation / "Papers" / "unknown.md"
    unknown.write_text("private note\n", encoding="utf-8", newline="\n")

    edited = service.status(session)
    assert edited["integrity_state"] == "edited_managed_file"
    assert "Papers/unknown.md" in edited["edited_paths"]
    with pytest.raises(ResearchKBError) as blocked:
        _render(service, session)
    assert blocked.value.diagnostic.code == "RKBC-039"

    repaired = _render(service, session, discard=True)
    repaired_status = service.status(session)
    repaired_root = layout.obsidian_generation_path(repaired_status["generation_id"])
    assert repaired["result"] == "committed"
    assert repaired_status["integrity_state"] == "intact"
    assert not (repaired_root / "Papers" / "unknown.md").exists()
    assert "private note" not in "\n".join(
        path.read_text(encoding="utf-8") for path in repaired_root.rglob("*.md")
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"workspace_id": "other-workspace"}), "another workspace"),
        (lambda value: value.update({"unexpected": True}), "unexpected fields"),
        (
            lambda value: value["files"][0].update(
                {"logical_path": "Papers/nested/evil.md"}
            ),
            "logical path",
        ),
    ],
)
def test_manifest_tamper_fails_closed(tmp_path: Path, mutation, message: str) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    _render(service, session)
    manifest = read_json_document(
        layout.obsidian_manifest_path,
        record_kind="obsidian-generated-view-manifest",
    )
    mutation(manifest)
    layout.obsidian_manifest_path.write_bytes(serialize_json(manifest))

    with pytest.raises(ResearchKBError, match=message) as caught:
        service.status(session)
    assert caught.value.diagnostic.code == "RKBC-038"


def test_lexical_managed_root_symlink_is_rejected_before_resolution(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    target = layout.knowledge_root / "synthetic-link-target"
    target.mkdir()
    layout.obsidian_views_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        layout.obsidian_views_root.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")

    with pytest.raises(ResearchKBError) as caught:
        service.status(session)
    assert caught.value.diagnostic.code == "RKBC-007"


def test_cli_and_application_service_share_the_same_projection_contract(
    tmp_path: Path,
    capsys,
) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    workspace = str(layout.config.path)

    assert main(["obsidian", "status", "--workspace", workspace]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing == service.status(session)

    assert main(
        [
            "obsidian",
            "render",
            "--workspace",
            workspace,
            "--table",
            "library_summary",
            "--dry-run",
        ]
    ) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["persistent_writes"] == 0
    assert preview["canonical_scientific_write"] is False
    assert str(tmp_path) not in json.dumps(preview)

    assert main(
        [
            "obsidian",
            "render",
            "--workspace",
            workspace,
            "--table",
            "library_summary",
        ]
    ) == 0
    rendered = json.loads(capsys.readouterr().out)
    assert rendered["result"] == "committed"
    assert rendered["application_service_interface_version"] == "1.23"

    assert main(["obsidian", "status", "--workspace", workspace]) == 0
    assert json.loads(capsys.readouterr().out) == service.status(session)


def test_status_uses_stable_bounded_logical_path_pagination(tmp_path: Path) -> None:
    _, session, service, _, _ = _workspace(tmp_path)
    _render(service, session)

    first = service.status(session, page_size=2)
    second = service.status(session, page_size=2, cursor=first["next_cursor"])
    assert len(first["entries"]) == 2
    assert len(second["entries"]) == 2
    assert first["entries"][-1]["logical_path"] == first["next_cursor"]
    assert {item["logical_path"] for item in first["entries"]}.isdisjoint(
        item["logical_path"] for item in second["entries"]
    )
    assert first["file_count"] > len(first["entries"])
    assert service.limits(session)["max_status_page_size"] == 100

    with pytest.raises(ResearchKBError):
        service.status(session, page_size=101)
    with pytest.raises(ResearchKBError):
        service.status(session, cursor="missing.md")


def test_snapshot_streams_verified_files_without_exposing_a_source_path(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    _render(service, session, tables=("library_summary",))
    status = service.status(session, page_size=100)
    streamed: list[tuple[str, str, bytes]] = []

    result = service.stream_snapshot(
        session,
        expected_manifest_digest=status["manifest_digest"],
        sink=lambda logical_path, content_digest, content: streamed.append(
            (logical_path, content_digest, content)
        ),
    )

    assert result["persistent_writes"] == 0
    assert result["canonical_scientific_write"] is False
    assert result["generation_id"] == status["generation_id"]
    assert result["manifest_digest"] == status["manifest_digest"]
    assert result["file_count"] == len(streamed) == status["file_count"]
    assert result["byte_count"] == sum(len(content) for _, _, content in streamed)
    assert [logical_path for logical_path, _, _ in streamed] == sorted(
        logical_path for logical_path, _, _ in streamed
    )
    assert all(sha256_bytes(content) == digest for _, digest, content in streamed)
    assert str(layout.config.path.parent).encode("utf-8") not in b"".join(
        content for _, _, content in streamed
    )


def test_snapshot_rejects_stale_manifest_and_midstream_source_change(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    _render(service, session)
    status = service.status(session, page_size=100)
    with pytest.raises(ResearchKBError) as stale:
        service.stream_snapshot(
            session,
            expected_manifest_digest="f" * 64,
            sink=lambda *_: None,
        )
    assert stale.value.diagnostic.code == SNAPSHOT_MISMATCH

    generation = layout.obsidian_generation_path(status["generation_id"])
    changed = False

    def mutate_after_first(logical_path: str, _digest: str, _content: bytes) -> None:
        nonlocal changed
        if not changed:
            changed = True
            (generation / logical_path).write_bytes(_content + b"\nchanged\n")

    with pytest.raises(ResearchKBError) as raced:
        service.stream_snapshot(
            session,
            expected_manifest_digest=status["manifest_digest"],
            sink=mutate_after_first,
        )
    assert raced.value.diagnostic.code == SNAPSHOT_MISMATCH


def test_unicode_titles_round_trip_without_becoming_link_syntax(tmp_path: Path) -> None:
    layout, session, service, _, _ = _workspace(tmp_path)
    papers = read_jsonl(layout.registry_path, record_kind="registry-paper", id_field="paper_id")
    papers[0]["bibliography"]["title"] = "合成 β 分型 [草稿] | 受控"
    layout.registry_path.write_bytes(serialize_jsonl(papers))

    _render(service, session)
    status = service.status(session)
    note = (
        layout.obsidian_generation_path(status["generation_id"])
        / "Papers"
        / f"{papers[0]['paper_id']}.md"
    ).read_text(encoding="utf-8")
    index = (
        layout.obsidian_generation_path(status["generation_id"])
        / "Papers"
        / "_index.md"
    ).read_text(encoding="utf-8")
    assert "合成 β 分型" in note
    assert "\\[草稿\\] \\| 受控" in note
    assert "合成 β 分型 \\[草稿\\] \\| 受控" in index
    assert "合成 β 分型 \\\\\\[草稿" not in index
