from __future__ import annotations

from pathlib import Path

import pytest

from research_kb.catalog.models import canonical_digest
from research_kb.errors import ResearchKBError
from research_kb.identity_corrections import project_registry_identity
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.registry import RegistryService
from research_kb.services.registry_identity import RegistryIdentityCorrectionService
from research_kb.storage.json_io import read_jsonl
from tests.runtime_helpers import make_runtime_workspace


def _paper(layout, name: str, content: bytes) -> str:
    source = layout.source_roots["alpha-sources"] / name
    source.write_bytes(content)
    return RegistryService(layout).add(
        root_id="alpha-sources",
        relative_path=name,
        metadata={"fixture_origin": "synthetic_from_scratch"},
    )[0]["paper_id"]


def _job(layout) -> dict:
    return PipelineJobService(layout).create(
        requested_route="local_source",
        requested_depth="registry_only",
        current_node="identity_correction",
        input_refs=[],
        authority_snapshot={
            "actor": "user",
            "granted_operations": ["registry_identity_correction"],
            "captured_at": "2026-07-30T05:30:00Z",
        },
        idempotency_key="identity-correction-test",
        actor="user",
        fixture_origin="synthetic_from_scratch",
    ).state


def test_merge_alias_archive_and_split_preserve_registry_rows(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _paper(layout, "first.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst identity\n%%EOF\n")
    second = _paper(layout, "second.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nsecond identity\n%%EOF\n")
    third = _paper(layout, "third.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nthird identity\n%%EOF\n")
    job = _job(layout)
    service = RegistryIdentityCorrectionService(layout)

    merged = service.record(
        job_id=job["job_id"],
        operation="confirmed_duplicate_merge",
        subject_paper_ids=[first, second],
        retained_paper_id=first,
        supersedes_correction_id=None,
        rationale="Synthetic duplicate decision.",
        expected_previous_correction_id=None,
        expected_previous_correction_digest=None,
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    aliased = service.record(
        job_id=job["job_id"],
        operation="paper_alias",
        subject_paper_ids=[third],
        retained_paper_id=first,
        supersedes_correction_id=None,
        rationale="Synthetic alias decision.",
        expected_previous_correction_id=merged.correction["correction_id"],
        expected_previous_correction_digest=canonical_digest(merged.correction),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    archived = service.record(
        job_id=job["job_id"],
        operation="library_archive",
        subject_paper_ids=[third],
        retained_paper_id=None,
        supersedes_correction_id=None,
        rationale="Synthetic archive decision.",
        expected_previous_correction_id=aliased.correction["correction_id"],
        expected_previous_correction_digest=canonical_digest(aliased.correction),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )
    split = service.record(
        job_id=job["job_id"],
        operation="mistaken_merge_split",
        subject_paper_ids=[second],
        retained_paper_id=None,
        supersedes_correction_id=merged.correction["correction_id"],
        rationale="Synthetic split correction.",
        expected_previous_correction_id=archived.correction["correction_id"],
        expected_previous_correction_digest=canonical_digest(archived.correction),
        actor="user",
        fixture_origin="synthetic_from_scratch",
    )

    registry = read_jsonl(layout.registry_path, record_kind="registry-paper")
    corrections = read_jsonl(layout.identity_corrections_path, record_kind="registry-identity-correction")
    assert {item["paper_id"] for item in registry} == {first, second, third}
    assert len(corrections) == 4
    projection = project_registry_identity(registry, corrections)
    assert projection[first]["canonical_paper_id"] == first
    assert projection[second]["canonical_paper_id"] == second
    assert projection[third]["canonical_paper_id"] == first
    assert projection[third]["library_status"] == "archived"
    assert split.correction["supersedes_correction_id"] == merged.correction["correction_id"]


def test_identity_correction_rejects_alias_cycle_and_stale_cas(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _paper(layout, "first.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst cycle\n%%EOF\n")
    second = _paper(layout, "second.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nsecond cycle\n%%EOF\n")
    job = _job(layout)
    service = RegistryIdentityCorrectionService(layout)
    alias = service.record(
        job_id=job["job_id"],
        operation="paper_alias",
        subject_paper_ids=[second],
        retained_paper_id=first,
        supersedes_correction_id=None,
        rationale="Synthetic alias.",
        expected_previous_correction_id=None,
        expected_previous_correction_digest=None,
        actor="user",
    )

    with pytest.raises(ResearchKBError) as stale:
        service.record(
            job_id=job["job_id"],
            operation="library_archive",
            subject_paper_ids=[first],
            retained_paper_id=None,
            supersedes_correction_id=None,
            rationale="Stale synthetic request.",
            expected_previous_correction_id=None,
            expected_previous_correction_digest=None,
            actor="user",
        )
    assert stale.value.diagnostic.code == "RKBC-017"

    with pytest.raises(ResearchKBError) as cycle:
        service.record(
            job_id=job["job_id"],
            operation="paper_alias",
            subject_paper_ids=[first],
            retained_paper_id=second,
            supersedes_correction_id=None,
            rationale="Synthetic cycle.",
            expected_previous_correction_id=alias.correction["correction_id"],
            expected_previous_correction_digest=canonical_digest(alias.correction),
            actor="user",
        )
    assert cycle.value.diagnostic.code in {"RKBC-009", "RKBC-017"}


def test_identity_correction_requires_user_and_retry_is_idempotent(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _paper(layout, "first.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst retry\n%%EOF\n")
    second = _paper(layout, "second.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nsecond retry\n%%EOF\n")
    job = _job(layout)
    service = RegistryIdentityCorrectionService(layout)
    request = {
        "job_id": job["job_id"],
        "operation": "confirmed_duplicate_merge",
        "subject_paper_ids": [second, first],
        "retained_paper_id": first,
        "supersedes_correction_id": None,
        "rationale": "Synthetic retry decision.",
        "expected_previous_correction_id": None,
        "expected_previous_correction_digest": None,
    }

    with pytest.raises(ResearchKBError) as authority:
        service.record(**request, actor="agent")
    assert authority.value.diagnostic.code == "RKBC-006"

    created = service.record(**request, actor="user")
    retried = service.record(**request, actor="user")

    assert created.transaction is not None
    assert retried.transaction is None
    assert retried.correction == created.correction
    assert len(read_jsonl(layout.identity_corrections_path, record_kind="registry-identity-correction")) == 1


def test_identity_correction_rejects_non_list_subjects_without_crashing(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _paper(
        layout,
        "first.pdf",
        bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst malformed subjects\n%%EOF\n",
    )
    job = _job(layout)

    with pytest.raises(ResearchKBError) as invalid:
        RegistryIdentityCorrectionService(layout).record(
            job_id=job["job_id"],
            operation="library_archive",
            subject_paper_ids=first,  # type: ignore[arg-type]
            retained_paper_id=None,
            supersedes_correction_id=None,
            rationale="Synthetic malformed request.",
            expected_previous_correction_id=None,
            expected_previous_correction_digest=None,
            actor="user",
        )

    assert invalid.value.diagnostic.code == "RKBC-002"
    assert not layout.identity_corrections_path.exists()


def test_identity_list_is_deterministic_and_tombstone_affects_projection_only(tmp_path: Path) -> None:
    layout = make_runtime_workspace(tmp_path)
    first = _paper(layout, "first.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nfirst list\n%%EOF\n")
    second = _paper(layout, "second.pdf", bytes((37, 80, 68, 70, 45)) + b"1.4\nsecond list\n%%EOF\n")
    job = _job(layout)
    service = RegistryIdentityCorrectionService(layout)
    created = service.record(
        job_id=job["job_id"],
        operation="library_tombstone",
        subject_paper_ids=[second],
        retained_paper_id=None,
        supersedes_correction_id=None,
        rationale="Synthetic lifecycle decision.",
        expected_previous_correction_id=None,
        expected_previous_correction_digest=None,
        actor="user",
    )

    first_result = service.list()
    second_result = service.list()

    assert first_result == second_result
    assert [item["paper_id"] for item in first_result["items"]] == sorted((first, second))
    tombstoned = next(item for item in first_result["items"] if item["paper_id"] == second)
    assert tombstoned["library_status"] == "tombstoned"
    assert first_result["current_correction_id"] == created.correction["correction_id"]
    assert len(read_jsonl(layout.registry_path, record_kind="registry-paper")) == 2
