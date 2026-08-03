from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.services.registry import RegistryService
from research_kb.services.tags import TagService
from research_kb.storage.transactions import TransactionManager
from research_kb.storage.json_io import read_json_document
from research_kb.tag_bundles import tag_bundle_diagnostics, tag_link_bundle_diagnostics
from tests.fixture_factory import make_bundle
from tests.runtime_helpers import make_runtime_workspace


def _approval() -> dict:
    return {
        "receipt_id": "synthetic-tag-approval",
        "approved_by": "user",
        "approved_at": "2026-08-03T00:00:00Z",
        "origin": "user_authored",
    }


def _entries() -> list[tuple[str, dict]]:
    fixture = make_bundle("alpha")
    return [(item["kind"], item["record"]) for item in fixture["records"]]


def _service(layout, entries):
    counters: dict[str, int] = {}

    def allocate(namespace):
        value = namespace.value
        counters[value] = counters.get(value, 0) + 1
        number = counters[value]
        return f"{value}_a{number:07d}-0000-4000-8000-{number:012d}"

    return TagService(layout, id_allocator=allocate, entries_loader=lambda _: deepcopy(entries))


class _BarrierTransactionManager(TransactionManager):
    def __init__(self, layout, barrier: Barrier):
        super().__init__(layout)
        self.barrier = barrier

    def promote_bytes(self, **kwargs):
        self.barrier.wait(timeout=5)
        return super().promote_bytes(**kwargs)


class _BeforeLockHookTransactionManager(TransactionManager):
    def __init__(self, layout, hook):
        super().__init__(layout)
        self.hook = hook

    def promote_bytes(self, **kwargs):
        hook, self.hook = self.hook, None
        if hook is not None:
            hook()
        return super().promote_bytes(**kwargs)


def test_tag_revision_rename_alias_archive_and_no_change(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout, _entries())

    first, first_tx = service.promote_tag(
        {"name": "Mechanism", "description": "Synthetic category.", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    repeated, repeated_tx = service.promote_tag(
        {"name": "  Mechanism  ", "description": "Synthetic category.", "aliases": [], "status": "active"},
        tag_id=first["tag_id"],
        approval=_approval(),
        actor="user",
        expected_revision_id=first["active_revision_id"],
        fixture_origin="synthetic_from_scratch",
    )
    assert first_tx is not None
    assert repeated_tx is None
    assert repeated == first

    renamed, rename_tx = service.promote_tag(
        {"name": "Mechanistic study", "description": "Synthetic category.", "aliases": [], "status": "active"},
        tag_id=first["tag_id"],
        approval=_approval(),
        actor="user",
        expected_revision_id=first["active_revision_id"],
        fixture_origin="synthetic_from_scratch",
    )
    assert rename_tx is not None
    assert renamed["revisions"][-1]["tag"]["aliases"] == ["Mechanism"]

    archived, archive_tx = service.promote_tag(
        {"status": "archived"},
        tag_id=first["tag_id"],
        approval=_approval(),
        actor="user",
        expected_revision_id=renamed["active_revision_id"],
        fixture_origin="synthetic_from_scratch",
    )
    assert archive_tx is not None
    assert archived["revisions"][-1]["tag"]["status"] == "archived"
    assert service.list_tags() == []
    assert service.list_tags(include_archived=True)[0]["tag_id"] == first["tag_id"]
    assert read_json_document(layout.tag_bundle_path(first["tag_id"])) == archived


def test_duplicate_normalized_name_or_alias_is_rejected(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout, _entries())
    service.promote_tag(
        {"name": "Clinical Translation", "description": "", "aliases": ["Clinic"], "status": "active"},
        approval=_approval(),
        actor="user",
    )

    with pytest.raises(ResearchKBError) as caught:
        service.promote_tag(
            {"name": " clinic ", "description": "", "aliases": [], "status": "active"},
            approval=_approval(),
            actor="user",
        )

    assert caught.value.diagnostic.code == "RKBC-004"


def test_caller_cannot_create_tag_with_unknown_stable_id(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout, _entries())

    with pytest.raises(ResearchKBError) as caught:
        service.promote_tag(
            {"name": "Caller identity", "description": "", "aliases": [], "status": "active"},
            tag_id="tag_f1111111-1111-4111-8111-111111111111",
            approval=_approval(),
            actor="user",
        )

    assert caught.value.diagnostic.code == "RKBC-005"


def test_bundle_local_revision_ids_are_unique_and_active_matches_once(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    bundle, _ = _service(layout, _entries()).promote_tag(
        {"name": "Revision identity", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )
    duplicate = deepcopy(bundle["revisions"][0])
    duplicate["revision_number"] = 2
    duplicate["predecessor"] = {
        "revision_id": bundle["revisions"][0]["revision_id"],
        "revision_digest": canonical_digest(bundle["revisions"][0]),
    }
    bundle["revisions"].append(duplicate)
    bundle["active_revision_id"] = duplicate["revision_id"]

    diagnostics = tag_bundle_diagnostics(bundle)

    assert any("unique" in item.message or "exactly once" in item.message for item in diagnostics)


def test_assignment_state_is_append_only_and_stable_across_target_revisions(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    tag, _ = service.promote_tag(
        {"name": "Priority", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")

    assigned, assign_tx = service.set_assignment(
        tag_id=tag["tag_id"],
        target_kind="paper",
        target_id=paper_id,
        state="assigned",
        approval=_approval(),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    repeated, repeated_tx = service.set_assignment(
        tag_id=tag["tag_id"],
        target_kind="paper",
        target_id=paper_id,
        state="assigned",
        approval=_approval(),
        actor="user",
        expected_revision_id=assigned["active_revision_id"],
        fixture_origin="synthetic_from_scratch",
    )
    assert assign_tx is not None
    assert repeated_tx is None
    assert repeated == assigned

    removed, remove_tx = service.set_assignment(
        tag_id=tag["tag_id"],
        target_kind="paper",
        target_id=paper_id,
        state="removed",
        approval=_approval(),
        actor="user",
        expected_revision_id=assigned["active_revision_id"],
        fixture_origin="synthetic_from_scratch",
    )
    assert remove_tx is not None
    assert removed["tag_link_id"] == assigned["tag_link_id"]
    assert len(removed["revisions"]) == 2
    assert service.list_assignments(tag_id=tag["tag_id"]) == []
    assert service.list_assignments(tag_id=tag["tag_id"], include_removed=True)[0]["state"] == "removed"

    tampered = deepcopy(removed)
    tampered["target_id"] = "paper_f1111111-1111-4111-8111-111111111111"
    assert any("content digest" in item.message for item in tag_link_bundle_diagnostics(tampered))


def test_archived_tag_existing_assignment_exact_replay_is_no_change(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    tag, _ = service.promote_tag(
        {"name": "Archive replay", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    assigned, _ = service.set_assignment(
        tag_id=tag["tag_id"], target_kind="paper", target_id=paper_id, state="assigned",
        approval=_approval(), actor="user",
    )
    service.promote_tag(
        {"status": "archived"}, tag_id=tag["tag_id"], approval=_approval(), actor="user",
        expected_revision_id=tag["active_revision_id"],
    )

    repeated, transaction = service.set_assignment(
        tag_id=tag["tag_id"], target_kind="paper", target_id=paper_id, state="assigned",
        approval=_approval(), actor="user", expected_revision_id=assigned["active_revision_id"],
    )

    assert repeated == assigned
    assert transaction is None


def test_concurrent_same_normalized_tag_creation_has_one_winner(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    barrier = Barrier(2)
    entries = _entries()
    services = [
        TagService(
            layout,
            transaction_manager=_BarrierTransactionManager(layout, barrier),
            entries_loader=lambda _: deepcopy(entries),
        )
        for _ in range(2)
    ]

    def create(service: TagService):
        try:
            bundle, _ = service.promote_tag(
                {"name": " Concurrent  Tag ", "description": "", "aliases": [], "status": "active"},
                approval=_approval(), actor="user",
            )
            return "committed", bundle["tag_id"]
        except ResearchKBError as error:
            return error.diagnostic.code, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, services))

    assert [item[0] for item in results].count("committed") == 1
    assert [item[0] for item in results].count("RKBC-004") == 1


def test_concurrent_same_assignment_creation_has_one_winner(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    tag, _ = TagService(layout, entries_loader=lambda _: deepcopy(entries)).promote_tag(
        {"name": "Concurrent assignment", "description": "", "aliases": [], "status": "active"},
        approval=_approval(), actor="user",
    )
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    barrier = Barrier(2)
    services = [
        TagService(
            layout,
            transaction_manager=_BarrierTransactionManager(layout, barrier),
            entries_loader=lambda _: deepcopy(entries),
        )
        for _ in range(2)
    ]

    def assign(service: TagService):
        try:
            bundle, _ = service.set_assignment(
                tag_id=tag["tag_id"], target_kind="paper", target_id=paper_id, state="assigned",
                approval=_approval(), actor="user",
            )
            return "committed", bundle["tag_link_id"]
        except ResearchKBError as error:
            return error.diagnostic.code, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(assign, services))

    assert [item[0] for item in results].count("committed") == 1
    assert [item[0] for item in results].count("RKBC-004") == 1


def test_assignment_rechecks_tag_status_inside_workspace_lock(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    tag_service = TagService(layout, entries_loader=lambda _: deepcopy(entries))
    tag, _ = tag_service.promote_tag(
        {"name": "Archive race", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")

    def archive_before_assignment_lock() -> None:
        tag_service.promote_tag(
            {"status": "archived"},
            tag_id=tag["tag_id"],
            expected_revision_id=tag["active_revision_id"],
            approval=_approval(),
            actor="user",
        )

    service = TagService(
        layout,
        transaction_manager=_BeforeLockHookTransactionManager(layout, archive_before_assignment_lock),
        entries_loader=lambda _: deepcopy(entries),
    )
    with pytest.raises(ResearchKBError) as caught:
        service.set_assignment(
            tag_id=tag["tag_id"],
            target_kind="paper",
            target_id=paper_id,
            state="assigned",
            approval=_approval(),
            actor="user",
        )

    assert caught.value.diagnostic.code == "RKBC-006"
    assert tag_service.list_assignments(tag_id=tag["tag_id"]) == []


@pytest.mark.parametrize("target_kind", ["paper", "direction", "field_map_entry", "question"])
def test_assignment_requires_existing_target(tmp_path: Path, target_kind: str) -> None:
    layout = make_runtime_workspace(tmp_path)
    service = _service(layout, _entries())
    tag, _ = service.promote_tag(
        {"name": "Synthetic", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )
    prefixes = {"paper": "paper", "direction": "direction", "field_map_entry": "fieldmap", "question": "question"}
    target_id = f"{prefixes[target_kind]}_f1111111-1111-4111-8111-111111111111"

    with pytest.raises(ResearchKBError) as caught:
        service.set_assignment(
            tag_id=tag["tag_id"],
            target_kind=target_kind,
            target_id=target_id,
            state="assigned",
            approval=_approval(),
            actor="user",
        )

    assert caught.value.diagnostic.code == "RKBC-005"


@pytest.mark.parametrize(
    ("target_kind", "record_kind", "id_field", "target_id"),
    [
        ("direction", "direction-bundle", "direction_id", "direction_f1111111-1111-4111-8111-111111111111"),
        ("field_map_entry", "field-map-bundle", "field_map_entry_id", "fieldmap_f1111111-1111-4111-8111-111111111111"),
        ("question", "question-revision-bundle", "question_id", "question_f1111111-1111-4111-8111-111111111111"),
        ("question", "question-mapping", "question_id", "question_f2222222-2222-4222-8222-222222222222"),
    ],
)
def test_assignment_accepts_canonical_and_legacy_organization_targets(
    tmp_path: Path,
    target_kind: str,
    record_kind: str,
    id_field: str,
    target_id: str,
) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = [*_entries(), (record_kind, {id_field: target_id})]
    service = _service(layout, entries)
    tag, _ = service.promote_tag(
        {"name": f"Target {target_kind} {record_kind}", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )

    assignment, transaction = service.set_assignment(
        tag_id=tag["tag_id"],
        target_kind=target_kind,
        target_id=target_id,
        state="assigned",
        approval=_approval(),
        actor="user",
    )

    assert transaction is not None
    assert assignment is not None
    assert assignment["target_id"] == target_id


def test_archived_tag_rejects_new_assignment_and_stale_expected_head(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    tag, _ = service.promote_tag(
        {"name": "Archived", "description": "", "aliases": [], "status": "archived"},
        approval=_approval(),
        actor="user",
    )
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    with pytest.raises(ResearchKBError) as caught:
        service.set_assignment(
            tag_id=tag["tag_id"], target_kind="paper", target_id=paper_id, state="assigned",
            approval=_approval(), actor="user",
        )
    assert caught.value.diagnostic.code == "RKBC-006"

    with pytest.raises(ResearchKBError) as stale:
        service.promote_tag(
            {"status": "active"}, tag_id=tag["tag_id"], approval=_approval(), actor="user",
            expected_revision_id="tagrev_f1111111-1111-4111-8111-111111111111",
        )
    assert stale.value.diagnostic.code == "RKBC-017"


def test_existing_tag_and_assignment_require_expected_heads(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    entries = _entries()
    service = _service(layout, entries)
    tag, _ = service.promote_tag(
        {"name": "Optimistic", "description": "", "aliases": [], "status": "active"},
        approval=_approval(),
        actor="user",
    )
    paper_id = next(record["paper_id"] for kind, record in entries if kind == "registry-paper")
    assignment, _ = service.set_assignment(
        tag_id=tag["tag_id"],
        target_kind="paper",
        target_id=paper_id,
        state="assigned",
        approval=_approval(),
        actor="user",
    )

    with pytest.raises(ResearchKBError) as tag_conflict:
        service.promote_tag(
            {"description": "Changed"},
            tag_id=tag["tag_id"],
            approval=_approval(),
            actor="user",
        )
    with pytest.raises(ResearchKBError) as assignment_conflict:
        service.set_assignment(
            tag_id=tag["tag_id"],
            target_kind="paper",
            target_id=paper_id,
            state="removed",
            approval=_approval(),
            actor="user",
        )

    assert tag_conflict.value.diagnostic.code == "RKBC-017"
    assert assignment_conflict.value.diagnostic.code == "RKBC-017"
    assert assignment is not None


def test_real_workspace_tag_assignment_is_guardian_clean(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    source = layout.source_roots["alpha-sources"] / "tagged-study.txt"
    source.write_text("Synthetic tagged source.\n", encoding="utf-8", newline="\n")
    paper, _ = RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=source.name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )
    service = TagService(layout)
    tag, _ = service.promote_tag(
        {"name": "Current", "description": "", "aliases": [], "status": "active"},
        approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch",
    )
    assignment, _ = service.set_assignment(
        tag_id=tag["tag_id"], target_kind="paper", target_id=paper["paper_id"], state="assigned",
        approval=_approval(), actor="user", fixture_origin="synthetic_from_scratch",
    )

    assert assignment is not None
    assert service.target_availability("paper", paper["paper_id"]) == "current"
    assert GuardianService(layout).check().report["status"] == "success"
