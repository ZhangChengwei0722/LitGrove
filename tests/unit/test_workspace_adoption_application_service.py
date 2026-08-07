from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services import WorkspaceAdoptionApplicationService
from research_kb.storage.json_io import read_json_document, serialize_json
from tests.runtime_helpers import make_runtime_workspace


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_workspace_adoption_inspection_is_stable_redacted_and_zero_write(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path, local_inbox="./sources/inbox", create_local_inbox=True)
    before = _tree_bytes(tmp_path)
    service = WorkspaceAdoptionApplicationService()

    first = service.inspect(layout.config.path)
    second = service.inspect(layout.config.path)

    assert first.basis_digest == second.basis_digest
    assert first.descriptor == second.descriptor
    assert first.descriptor["admissible"] is True
    assert first.descriptor["guardian"] == {
        "status": "success",
        "finding_count": 0,
        "error_count": 0,
        "warning_count": 0,
    }
    assert first.descriptor["transaction_recovery"] == {
        "status": "current",
        "action_count": 0,
    }
    assert first.descriptor["application_service_interface_version"] == "1.21"
    assert "path" not in str(first.descriptor).lower()
    assert first.writable_roots.knowledge_root == layout.knowledge_root.resolve()
    assert _tree_bytes(tmp_path) == before


def test_workspace_adoption_missing_inbox_is_controlled_ineligible_and_zero_write(tmp_path: Path) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=False,
    )
    before = _tree_bytes(tmp_path)

    inspection = WorkspaceAdoptionApplicationService().inspect(layout.config.path)

    assert inspection.descriptor["status"] == "success"
    assert inspection.descriptor["adoption_status"] == "ineligible"
    assert inspection.descriptor["admissible"] is False
    assert inspection.descriptor["ineligibility_reasons"] == ["local_inbox_missing"]
    assert inspection.descriptor["persistent_writes"] == 0
    assert inspection.descriptor["canonical_scientific_write"] is False
    assert _tree_bytes(tmp_path) == before


@pytest.mark.parametrize("protected_input", ["config", "profile", "marker"])
def test_workspace_adoption_rejects_protected_input_drift_after_guardian(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    protected_input: str,
) -> None:
    layout = make_runtime_workspace(
        tmp_path,
        local_inbox="./sources/inbox",
        create_local_inbox=True,
    )
    path_by_input = {
        "config": layout.config.path,
        "profile": layout.domain_profile_path,
        "marker": layout.marker_path,
    }
    path = path_by_input[protected_input]
    original = path.read_bytes()

    original_check = GuardianService.check

    def check_and_mutate(self, *args, **kwargs):
        result = original_check(self, *args, **kwargs)
        if protected_input == "marker":
            marker = read_json_document(path, record_kind="workspace-marker")
            marker["config_fingerprint"]["value"] = "0" * 64
            path.write_bytes(serialize_json(marker))
        else:
            path.write_bytes(original + b"\n")
        return result

    monkeypatch.setattr(GuardianService, "check", check_and_mutate)
    try:
        with pytest.raises(ResearchKBError) as caught:
            WorkspaceAdoptionApplicationService().inspect(layout.config.path)
        assert caught.value.diagnostic.code == "RKBC-026"
    finally:
        path.write_bytes(original)

    assert _tree_bytes(tmp_path)[path.relative_to(tmp_path).as_posix()] == original
