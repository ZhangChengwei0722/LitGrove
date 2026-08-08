from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.services.tag_application import TagApplicationService
from research_kb.services.workspace_session import WorkspaceSessionService
from tests.runtime_helpers import make_runtime_workspace


def test_tag_application_create_list_show_and_no_change(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    service = TagApplicationService()

    created = service.promote_tag(session, {
        "name": "Methods",
        "description": "Synthetic method grouping.",
        "aliases": [],
        "status": "active",
        "receipt_id": "tag-create",
    })
    listed = service.list_tags(session)
    shown = service.show_tag(session, created["tag"]["tag_id"])
    repeated = service.promote_tag(session, {
        "tag_id": created["tag"]["tag_id"],
        "name": "Methods",
        "description": "Synthetic method grouping.",
        "aliases": [],
        "status": "active",
        "expected_revision_id": created["tag"]["revision_id"],
        "receipt_id": "tag-repeat",
    })

    assert created["result"] == "committed"
    assert created["application_service_interface_version"] == "1.23"
    assert created["canonical_scientific_write"] is False
    assert listed["tags"] == [created["tag"]]
    assert shown["assignments"] == []
    assert repeated["result"] == "no_change"
    assert not _forbidden_keys({"created": created, "listed": listed, "shown": shown})


def test_tag_application_rejects_fake_session_and_open_request(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    service = TagApplicationService()

    with pytest.raises(ResearchKBError):
        service.limits(object())  # type: ignore[arg-type]
    with pytest.raises(ResearchKBError):
        service.promote_tag(session, {"name": "Unsafe", "receipt_id": "x", "extra": True})
    with pytest.raises(ResearchKBError):
        service.list_tags(session, page_size=101)
    with pytest.raises(ResearchKBError):
        service.promote_tag(session, {"name": "Unsafe", "receipt_id": {"unexpected": "object"}})


def test_tag_list_cursor_is_stable_across_rename(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    service = TagApplicationService()
    first = service.promote_tag(session, {"name": "Zulu", "receipt_id": "first"})["tag"]
    second = service.promote_tag(session, {"name": "Alpha", "receipt_id": "second"})["tag"]
    ordered = sorted((first, second), key=lambda item: item["tag_id"])

    page_one = service.list_tags(session, page_size=1)
    renamed = service.promote_tag(session, {
        "tag_id": page_one["tags"][0]["tag_id"],
        "name": "Renamed",
        "expected_revision_id": page_one["tags"][0]["revision_id"],
        "receipt_id": "rename",
    })
    page_two = service.list_tags(session, page_size=1, cursor=page_one["next_cursor"])

    assert renamed["result"] == "committed"
    assert page_one["tags"][0]["tag_id"] == ordered[0]["tag_id"]
    assert page_two["tags"][0]["tag_id"] == ordered[1]["tag_id"]


def _forbidden_keys(value: object) -> set[str]:
    forbidden = {"source_ref", "source_fingerprint", "path", "approval", "transaction"}
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(forbidden & set(value))
        for item in value.values():
            found.update(_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_forbidden_keys(item))
    return found
