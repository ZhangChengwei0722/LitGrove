from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.services.research_organization_application import (
    ResearchOrganizationApplicationService,
)
from research_kb.services.workspace_session import WorkspaceSessionService
from tests.runtime_helpers import make_runtime_workspace


APPROVAL = {
    "receipt_id": "user-authored-p7a-test",
    "approved_by": "user",
    "approved_at": "2026-01-01T00:00:00Z",
    "origin": "user_authored",
}


def test_session_bound_application_lists_safe_direction_and_empty_questions(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    bundle, _ = ResearchOrganizationService(layout).promote_direction(
        {
            "name": "Synthetic direction",
            "scope": "Synthetic scope only.",
            "status": "active",
            "unit_links": [],
            "gap_notes": ["Synthetic gap."],
        },
        approval=APPROVAL,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    service = ResearchOrganizationApplicationService()

    listed = service.list_directions(session, page_size=1)
    shown = service.show_direction(session, bundle["direction_id"])
    questions = service.list_questions(session, page_size=100)

    assert listed["directions"][0]["direction_id"] == shown["direction"]["direction_id"]
    assert "links" not in listed["directions"][0]
    assert shown["direction"]["links_truncated"] is False
    assert questions["questions"] == []
    assert listed["application_service_interface_version"] == "1.12"
    assert listed["persistent_writes"] == 0
    assert listed["canonical_scientific_write"] is False
    assert not _forbidden_keys(listed)
    assert GuardianService(layout).check().report["status"] == "success"


def test_application_rejects_fake_session_and_invalid_page_size(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    session = WorkspaceSessionService({"alpha": layout.config.path}).open("alpha")
    service = ResearchOrganizationApplicationService()

    with pytest.raises(ResearchKBError):
        service.limits(object())  # type: ignore[arg-type]
    with pytest.raises(ResearchKBError):
        service.list_directions(session, page_size=101)


def _forbidden_keys(value: object) -> set[str]:
    forbidden = {
        "source_ref",
        "source_fingerprint",
        "path",
        "task_result_digest",
        "approval",
        "transaction",
    }
    found: set[str] = set()
    if isinstance(value, dict):
        found.update(forbidden & set(value))
        for item in value.values():
            found.update(_forbidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_forbidden_keys(item))
    return found
