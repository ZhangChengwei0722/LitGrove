from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from research_kb.compatibility import (
    CompatibilitySourceRef,
    DifferenceCandidate,
    InventoryCandidate,
)
from research_kb.compatibility.models import apply_difference_policy, build_difference_id
from research_kb.contracts.validator import validate_record
from research_kb.errors import ResearchKBError
from research_kb.services.compatibility import CompatibilityAdapterRegistry, CompatibilityInspectionService
from research_kb.storage.json_io import serialize_json
from tests.compatibility_helpers import (
    CleanLegacyAdapter,
    SyntheticLegacyAdapter,
    make_compatibility_workspace,
)


ALL_DISPOSITIONS = {
    "direct_read",
    "adapter_projection",
    "unsupported_for_now",
    "legacy_reading_view",
}
ALL_DIFFERENCE_TYPES = {
    "representation_only",
    "field_mapping_loss",
    "provenance_break",
    "semantic_mismatch",
    "status_authority_mismatch",
    "unsupported_legacy_view",
}


def test_compatibility_report_covers_public_contract_and_opaque_ids(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    adapter = SyntheticLegacyAdapter()
    result = CompatibilityInspectionService(
        layout,
        CompatibilityAdapterRegistry([adapter]),
    ).inspect(adapter.adapter_id)

    report = result.report
    assert validate_record("compatibility-report", report, actor="stored") == []
    assert {item["disposition"] for item in report["items"]} == ALL_DISPOSITIONS
    assert {item["difference_type"] for item in report["differences"]} == ALL_DIFFERENCE_TYPES
    assert report["items"][0]["legacy_identity"]["legacy_id"].startswith("ALPHA legacy")
    assert report["blocking_difference_count"] == 4
    assert report["status"] == "blocking_differences"
    assert result.exit_code == 1
    output = serialize_json(report).decode("utf-8")
    assert "must not leave the adapter" not in output
    assert str(tmp_path) not in output

    with_extra = dict(report)
    with_extra["unexpected"] = True
    assert validate_record("compatibility-report", with_extra, actor="stored")
    missing_required = dict(report)
    missing_required.pop("read_only")
    assert validate_record("compatibility-report", missing_required, actor="stored")
    invalid_ref = json.loads(json.dumps(report))
    invalid_ref["items"][0]["source_ref"]["relative_path"] = "C:" + "/private/item.json"
    assert validate_record("compatibility-report", invalid_ref, actor="stored")


def test_difference_id_is_deterministic_and_exact() -> None:
    values = {
        "source_system": "synthetic-legacy",
        "record_kind": "legacy-paper",
        "legacy_id": "opaque ID #1",
        "difference_type": "representation_only",
        "field_path": "/source~1path",
        "legacy_value_digest": "a" * 64,
        "projected_value_digest": "b" * 64,
    }
    first = build_difference_id(**values)
    second = build_difference_id(**dict(reversed(list(values.items()))))
    assert first == second
    assert first.startswith("diff_sha256_")
    assert len(first) == len("diff_sha256_") + 64


@pytest.mark.parametrize(
    ("difference_type", "severity", "record_role", "loss_scope", "expected"),
    [
        ("representation_only", "warning", "canonical", None, ("warning", False)),
        ("provenance_break", "info", "canonical", "provenance", ("error", True)),
        ("status_authority_mismatch", "warning", "candidate", "authority", ("error", True)),
        ("semantic_mismatch", "warning", "candidate", None, ("warning", True)),
        ("semantic_mismatch", "warning", "other", None, ("warning", False)),
        ("field_mapping_loss", "warning", "other", "evidence_support", ("warning", True)),
        ("field_mapping_loss", "warning", "other", "other", ("warning", False)),
        ("unsupported_legacy_view", "info", "reading_view", None, ("info", False)),
        ("unsupported_legacy_view", "info", "canonical", None, ("info", True)),
    ],
)
def test_core_owns_mandatory_difference_policy(
    difference_type: str,
    severity: str,
    record_role: str,
    loss_scope: str | None,
    expected: tuple[str, bool],
) -> None:
    assert apply_difference_policy(difference_type, severity, record_role, loss_scope) == expected


def test_adapter_iteration_order_does_not_change_report(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    first = SyntheticLegacyAdapter()
    reversed_adapter = SyntheticLegacyAdapter(reverse=True)
    first_report = CompatibilityInspectionService(
        layout, CompatibilityAdapterRegistry([first])
    ).inspect(first.adapter_id).report
    reversed_report = CompatibilityInspectionService(
        layout, CompatibilityAdapterRegistry([reversed_adapter])
    ).inspect(reversed_adapter.adapter_id).report
    assert serialize_json(first_report) == serialize_json(reversed_report)


def test_duplicate_and_unknown_adapters_fail_closed(tmp_path: Path) -> None:
    adapter = SyntheticLegacyAdapter()
    with pytest.raises(ResearchKBError) as duplicate:
        CompatibilityAdapterRegistry([adapter, adapter])
    assert duplicate.value.diagnostic.code == "RKBC-004"

    layout = make_compatibility_workspace(tmp_path)
    with pytest.raises(ResearchKBError) as unknown:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([])).inspect("missing-adapter")
    assert unknown.value.diagnostic.code == "RKBC-024"


def test_duplicate_legacy_identity_and_difference_id_fail_closed(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)

    class DuplicateIdentityAdapter(SyntheticLegacyAdapter):
        def iter_inventory(self, context):
            first = next(iter(super().iter_inventory(context)))
            yield first
            yield first

    adapter = DuplicateIdentityAdapter()
    with pytest.raises(ResearchKBError) as duplicate_identity:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert duplicate_identity.value.diagnostic.code == "RKBC-004"

    class DuplicateDifferenceAdapter(SyntheticLegacyAdapter):
        def iter_inventory(self, context):
            first = next(iter(super().iter_inventory(context)))
            yield InventoryCandidate(
                record_kind=first.record_kind,
                legacy_id=first.legacy_id,
                source_ref=first.source_ref,
                disposition=first.disposition,
                projected_kind=first.projected_kind,
                projection_status=first.projection_status,
                record_role=first.record_role,
                unsupported_fields=first.unsupported_fields,
                diagnostic_codes=first.diagnostic_codes,
                differences=(first.differences[0], first.differences[0]),
            )

    difference_adapter = DuplicateDifferenceAdapter()
    with pytest.raises(ResearchKBError) as duplicate_difference:
        CompatibilityInspectionService(
            layout, CompatibilityAdapterRegistry([difference_adapter])
        ).inspect(difference_adapter.adapter_id)
    assert duplicate_difference.value.diagnostic.code == "RKBC-004"


def test_source_mutation_takes_precedence_over_adapter_error(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)

    class MutatingAdapter(SyntheticLegacyAdapter):
        def iter_inventory(self, context):
            source = context.resolve_source(CompatibilitySourceRef(self.root_id, "legacy.jsonl"))
            source.write_text("mutated\n", encoding="utf-8", newline="\n")
            raise ValueError("injected adapter failure")
            yield

    adapter = MutatingAdapter()
    with pytest.raises(ResearchKBError) as changed:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert changed.value.diagnostic.code == "RKBC-026"


def test_source_type_change_is_detected(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)

    class TypeChangingAdapter(CleanLegacyAdapter):
        def iter_inventory(self, context):
            source = context.resolve_source(CompatibilitySourceRef(self.root_id, "legacy.jsonl"))
            source.rename(source.with_name("legacy-original.jsonl"))
            source.mkdir()
            yield from ()

    adapter = TypeChangingAdapter()
    with pytest.raises(ResearchKBError) as changed:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert changed.value.diagnostic.code == "RKBC-026"


def test_source_replaced_by_link_is_detected(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    source_root = layout.source_roots["alpha-sources"]
    source = source_root / "legacy.jsonl"
    try:
        (source_root / "link-support-probe").symlink_to(source)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlink creation is unavailable on this host: {error}")

    class LinkReplacingAdapter(CleanLegacyAdapter):
        def iter_inventory(self, context):
            source = context.resolve_source(CompatibilitySourceRef(self.root_id, "legacy.jsonl"))
            original = source.with_name("legacy-original.jsonl")
            source.rename(original)
            source.symlink_to(original)
            yield from ()

    adapter = LinkReplacingAdapter()
    with pytest.raises(ResearchKBError) as changed:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert changed.value.diagnostic.code == "RKBC-026"


def test_tree_snapshot_is_deterministic_and_tracks_empty_directories(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    tree = layout.source_roots["alpha-sources"] / "legacy-tree"
    (tree / "empty").mkdir(parents=True)
    record = tree / "records" / "item.txt"
    record.parent.mkdir()
    record.write_text("opaque tree record", encoding="utf-8", newline="\n")

    class TreeAdapter(CleanLegacyAdapter):
        def protected_inputs(self, context):
            del context
            return (CompatibilitySourceRef(self.root_id, "legacy-tree"),)

        def iter_inventory(self, context):
            source_ref = CompatibilitySourceRef(self.root_id, "legacy-tree/records/item.txt")
            legacy_id = context.resolve_source(source_ref).read_text(encoding="utf-8")
            yield InventoryCandidate(
                record_kind="legacy-paper",
                legacy_id=legacy_id,
                source_ref=source_ref,
                disposition="direct_read",
                projected_kind="paper-card",
                projection_status="complete",
                record_role="canonical",
                unsupported_fields=(),
                diagnostic_codes=(),
                differences=(),
            )

    adapter = TreeAdapter()
    service = CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter]))
    first = service.inspect(adapter.adapter_id).report["source_snapshot_before"][0]
    second = service.inspect(adapter.adapter_id).report["source_snapshot_before"][0]
    assert first == second
    assert first["source_kind"] == "tree"

    (tree / "later-empty").mkdir()
    third = service.inspect(adapter.adapter_id).report["source_snapshot_before"][0]
    assert third["value"] != first["value"]


def test_tree_snapshot_rejects_nested_links(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    source_root = layout.source_roots["alpha-sources"]
    tree = source_root / "legacy-tree"
    tree.mkdir()
    target = source_root / "link-target.txt"
    target.write_text("synthetic link target", encoding="utf-8", newline="\n")
    try:
        (tree / "linked.txt").symlink_to(target)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlink creation is unavailable on this host: {error}")

    class LinkedTreeAdapter(CleanLegacyAdapter):
        def protected_inputs(self, context):
            del context
            return (CompatibilitySourceRef(self.root_id, "legacy-tree"),)

        def iter_inventory(self, context):
            del context
            yield from ()

    adapter = LinkedTreeAdapter()
    with pytest.raises(ResearchKBError) as unsafe:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert unsafe.value.diagnostic.code == "RKBC-025"


def test_protected_input_rejects_a_link_reference(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    source_root = layout.source_roots["alpha-sources"]
    alias = source_root / "legacy-alias.jsonl"
    try:
        alias.symlink_to(source_root / "legacy.jsonl")
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symlink creation is unavailable on this host: {error}")

    class LinkedInputAdapter(CleanLegacyAdapter):
        def protected_inputs(self, context):
            del context
            return (CompatibilitySourceRef(self.root_id, "legacy-alias.jsonl"),)

    adapter = LinkedInputAdapter()
    with pytest.raises(ResearchKBError) as unsafe:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert unsafe.value.diagnostic.code == "RKBC-025"


def test_successful_inspection_is_read_only(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    adapter = CleanLegacyAdapter()
    source = layout.source_roots[adapter.root_id] / "legacy.jsonl"
    source_before = source.read_bytes()
    knowledge_before = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    result = CompatibilityInspectionService(
        layout, CompatibilityAdapterRegistry([adapter])
    ).inspect(adapter.adapter_id)
    knowledge_after = {
        path.relative_to(layout.knowledge_root).as_posix(): path.read_bytes()
        for path in layout.knowledge_root.rglob("*")
        if path.is_file()
    }
    assert result.exit_code == 0
    assert result.report["status"] == "success"
    assert result.report["source_snapshot_before"] == result.report["source_snapshot_after"]
    assert source.read_bytes() == source_before
    assert knowledge_after == knowledge_before
    assert not layout.process_events_path.exists()
    assert list(layout.transactions_root.glob("*.json")) == []


def test_invalid_adapter_metadata_and_absolute_legacy_id_are_rejected(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)

    with pytest.raises(ResearchKBError) as incomplete:
        CompatibilityAdapterRegistry([object()])
    assert incomplete.value.diagnostic.code == "RKBC-024"

    class UnsupportedAdapter(CleanLegacyAdapter):
        supported_contract_versions = ("2.0",)

    with pytest.raises(ResearchKBError) as unsupported:
        CompatibilityAdapterRegistry([UnsupportedAdapter()])
    assert unsupported.value.diagnostic.code == "RKBC-024"

    class PathVersionAdapter(CleanLegacyAdapter):
        adapter_version = "Z:" + chr(92) + "private"

    with pytest.raises(ResearchKBError) as path_version:
        CompatibilityAdapterRegistry([PathVersionAdapter()])
    assert path_version.value.diagnostic.code == "RKBC-024"

    class PrivateIdAdapter(CleanLegacyAdapter):
        def iter_inventory(self, context):
            first = next(iter(super().iter_inventory(context)))
            private_id = "Z:" + "/private/legacy-record"
            yield InventoryCandidate(
                record_kind=first.record_kind,
                legacy_id=private_id,
                source_ref=first.source_ref,
                disposition=first.disposition,
                projected_kind=first.projected_kind,
                projection_status=first.projection_status,
                record_role=first.record_role,
                unsupported_fields=(),
                diagnostic_codes=(),
                differences=(),
            )

    private = PrivateIdAdapter()
    with pytest.raises(ResearchKBError) as leaked:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([private])).inspect(private.adapter_id)
    assert leaked.value.diagnostic.code == "RKBC-025"

    class PrivateMessageAdapter(SyntheticLegacyAdapter):
        def iter_inventory(self, context):
            first = next(iter(super().iter_inventory(context)))
            private_message = "Inspect " + "/private/research/file.txt"
            yield replace(first, differences=(replace(first.differences[0], message=private_message),))

    private_message = PrivateMessageAdapter()
    with pytest.raises(ResearchKBError) as leaked_message:
        CompatibilityInspectionService(
            layout, CompatibilityAdapterRegistry([private_message])
        ).inspect(private_message.adapter_id)
    assert leaked_message.value.diagnostic.code == "RKBC-025"


def test_malformed_protected_ref_and_candidate_fail_as_invalid_output(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)

    class MalformedProtectedAdapter(CleanLegacyAdapter):
        def protected_inputs(self, context):
            del context
            return (object(),)

    protected = MalformedProtectedAdapter()
    with pytest.raises(ResearchKBError) as malformed_ref:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([protected])).inspect(protected.adapter_id)
    assert malformed_ref.value.diagnostic.code == "RKBC-025"

    class EscapingProtectedAdapter(CleanLegacyAdapter):
        def protected_inputs(self, context):
            del context
            return (CompatibilitySourceRef(self.root_id, "../legacy.jsonl"),)

    escaping = EscapingProtectedAdapter()
    with pytest.raises(ResearchKBError) as escaping_ref:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([escaping])).inspect(escaping.adapter_id)
    assert escaping_ref.value.diagnostic.code == "RKBC-025"

    class MalformedCandidateAdapter(CleanLegacyAdapter):
        def iter_inventory(self, context):
            del context
            yield object()

    candidate = MalformedCandidateAdapter()
    with pytest.raises(ResearchKBError) as malformed_candidate:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([candidate])).inspect(candidate.adapter_id)
    assert malformed_candidate.value.diagnostic.code == "RKBC-025"


def test_output_reference_must_be_covered_by_protected_inputs(tmp_path: Path) -> None:
    layout = make_compatibility_workspace(tmp_path)
    unprotected = layout.source_roots["alpha-sources"] / "unprotected.txt"
    unprotected.write_text("synthetic unprotected record", encoding="utf-8", newline="\n")

    class UncoveredReferenceAdapter(CleanLegacyAdapter):
        def iter_inventory(self, context):
            first = next(iter(super().iter_inventory(context)))
            yield replace(
                first,
                source_ref=CompatibilitySourceRef(self.root_id, "unprotected.txt"),
            )

    adapter = UncoveredReferenceAdapter()
    with pytest.raises(ResearchKBError) as uncovered:
        CompatibilityInspectionService(layout, CompatibilityAdapterRegistry([adapter])).inspect(adapter.adapter_id)
    assert uncovered.value.diagnostic.code == "RKBC-025"
