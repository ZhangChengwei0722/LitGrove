from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb.acquisition_paths import local_inbox_destination
from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import (
    DUPLICATE_ID,
    GROUNDING_MISMATCH,
    INVALID_AUTHORITY,
    PATH_ESCAPE,
    SCHEMA_VALIDATION_FAILED,
    UNRESOLVED_REFERENCE,
    WRITE_CONFLICT,
    Diagnostic,
    ResearchKBError,
)
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.process_events import timestamp
from research_kb.services._pipeline_authority import require_job_authority
from research_kb.source_resolution import SourceRefObservation, inspect_source_ref
from research_kb.source_assets import current_source_asset_heads, source_asset_chain_diagnostics, source_asset_projection
from research_kb.storage.json_io import file_sha256, read_jsonl, serialize_jsonl
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
SourceValidator = Callable[[], None]


@dataclass(frozen=True, slots=True)
class SourceAssetMutationResult:
    state: dict[str, Any]
    transaction: TransactionResult | None


class SourceAssetService:
    def __init__(
        self,
        layout: WorkspaceLayout,
        *,
        transaction_manager: TransactionManager | None = None,
        id_allocator: IdAllocator = allocate_id,
    ):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout)
        self.id_allocator = id_allocator

    def list(self) -> dict[str, Any]:
        states = self._read_states()
        return {
            "status": "success",
            "interface_version": "1.0",
            "source_assets": list(source_asset_projection(states)),
            "count": len({item["source_asset_id"] for item in states}),
            "persistent_writes": 0,
        }

    def register_reference(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        root_id: str,
        relative_path: str,
        actor: str,
        fixture_origin: str | None = None,
    ) -> SourceAssetMutationResult:
        return self._register_existing_source(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            root_id=root_id,
            relative_path=relative_path,
            actor=actor,
            fixture_origin=fixture_origin,
            authority_operation="register_by_reference",
            reason="reference_registered",
            transaction_operation="source_asset_register_reference",
        )

    def register_copied_inbox(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        root_id: str,
        relative_path: str,
        actor: str,
        fixture_origin: str | None = None,
    ) -> SourceAssetMutationResult:
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "source-asset-state", None, "/actor", "inbox copy requires exact user authority")
            )
        return self._register_existing_source(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            root_id=root_id,
            relative_path=relative_path,
            actor=actor,
            fixture_origin=fixture_origin,
            authority_operation="copy_into_local_inbox",
            reason="copied_into_local_inbox",
            transaction_operation="source_asset_copy_into_local_inbox",
        )

    def register_staged_inbox(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        source_hash: str,
        actor: str,
        validate_staged_source: SourceValidator,
        fixture_origin: str | None = None,
    ) -> SourceAssetMutationResult:
        require_job_authority(self.layout, job_id, "copy_into_local_inbox")
        if actor != "user":
            raise ResearchKBError(
                Diagnostic(INVALID_AUTHORITY, "source-asset-state", None, "/actor", "inbox copy requires exact user authority")
            )
        destination = local_inbox_destination(self.layout, f"{job_id}.pdf")
        validate_staged_source()
        return self._register_manifestation(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            source_ref=destination.source_ref.to_dict(),
            source_hash=source_hash,
            actor=actor,
            fixture_origin=fixture_origin,
            reason="copied_into_local_inbox",
            transaction_operation="source_asset_copy_into_local_inbox",
            validate_source=validate_staged_source,
        )

    def associate(
        self,
        *,
        source_asset_id: str,
        job_id: str,
        paper_id: str,
        expected_state_id: str,
        expected_state_digest: str,
        actor: str,
    ) -> SourceAssetMutationResult:
        require_job_authority(self.layout, job_id, "associate_source_asset")
        _require_actor(actor)
        paper_id = validate_id(paper_id, Namespace.PAPER)
        head, states = self._require_head(
            source_asset_id,
            expected_state_id,
            expected_state_digest,
        )
        if head["paper_id"] is not None:
            if head["paper_id"] == paper_id:
                return SourceAssetMutationResult(head, None)
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "source-asset-state", expected_state_id, "/paper_id", "source asset is already associated with another paper")
            )
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        papers = {
            record["paper_id"]: record
            for record in records_of_kind(entries, "registry-paper")
        }
        paper = papers.get(paper_id)
        if paper is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "source-asset-state", expected_state_id, "/paper_id", "source asset paper does not exist")
            )
        _require_main_manifestation_match(
            asset_role=head["asset_role"],
            source_hash=head["source_fingerprint"]["value"],
            paper=paper,
            record_id=expected_state_id,
        )
        observation = inspect_source_ref(
            self.layout,
            root_id=head["source_ref"]["root_id"],
            relative_path=head["source_ref"]["relative_path"],
        )
        if (
            observation.availability != "available"
            or observation.live_sha256 != head["source_fingerprint"]["value"]
            or head["availability"] != "available"
            or head["manifestation_status"] != "active"
        ):
            raise ResearchKBError(
                Diagnostic(GROUNDING_MISMATCH, "source-asset-state", expected_state_id, "/source_fingerprint", "only one current active manifestation can be associated with a paper")
            )
        state = self._successor(
            head,
            job_id=job_id,
            actor=actor,
            source_ref=head["source_ref"],
            source_hash=head["source_fingerprint"]["value"],
            manifestation_status="active",
            availability="available",
            reason="paper_associated",
        )
        state["paper_id"] = paper_id
        return self._append(
            states,
            state,
            operation="source_asset_associate",
            actor=actor,
            source=observation.path,
            expected_source_hash=observation.live_sha256,
        )

    def _register_existing_source(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        root_id: str,
        relative_path: str,
        actor: str,
        fixture_origin: str | None,
        authority_operation: str,
        reason: str,
        transaction_operation: str,
    ) -> SourceAssetMutationResult:
        require_job_authority(self.layout, job_id, authority_operation)
        _require_actor(actor)
        if paper_id is not None:
            paper_id = validate_id(paper_id, Namespace.PAPER)
        observation = inspect_source_ref(
            self.layout,
            root_id=root_id,
            relative_path=relative_path,
        )
        source_ref, source, source_hash = _require_available_source(observation)

        def validate_source() -> None:
            current = inspect_source_ref(
                self.layout,
                root_id=source_ref.root_id,
                relative_path=source_ref.relative_path,
            )
            if current.availability != "available" or current.live_sha256 != source_hash:
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "source-asset-state",
                        None,
                        "/source_fingerprint",
                        "source changed during source asset operation",
                    )
                )

        return self._register_manifestation(
            job_id=job_id,
            paper_id=paper_id,
            asset_role=asset_role,
            source_ref=source_ref.to_dict(),
            source_hash=source_hash,
            actor=actor,
            fixture_origin=fixture_origin,
            reason=reason,
            transaction_operation=transaction_operation,
            validate_source=validate_source,
        )

    def _register_manifestation(
        self,
        *,
        job_id: str,
        paper_id: str | None,
        asset_role: str,
        source_ref: dict[str, str],
        source_hash: str,
        actor: str,
        fixture_origin: str | None,
        reason: str,
        transaction_operation: str,
        validate_source: SourceValidator,
    ) -> SourceAssetMutationResult:
        if paper_id is not None:
            paper_id = validate_id(paper_id, Namespace.PAPER)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        papers = {
            record["paper_id"]: record
            for record in records_of_kind(entries, "registry-paper")
        }
        paper = None if paper_id is None else papers.get(paper_id)
        if paper_id is not None and paper is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "source-asset-state", None, "/paper_id", "source asset paper does not exist")
            )
        if paper is not None:
            _require_main_manifestation_match(
                asset_role=asset_role,
                source_hash=source_hash,
                paper=paper,
                record_id=None,
            )
        states = records_of_kind(entries, "source-asset-state")
        heads = {
            item["source_asset_id"]: item for item in current_source_asset_heads(states)
        }
        for root in states:
            if (
                root["revision"] == 1
                and root["job_id"] == job_id
                and root["paper_id"] == paper_id
                and root["asset_role"] == asset_role
                and root["source_ref"] == source_ref
                and root["reason"] == reason
            ):
                if root["source_fingerprint"]["value"] != source_hash:
                    raise ResearchKBError(
                        Diagnostic(
                            GROUNDING_MISMATCH,
                            "source-asset-state",
                            root["source_asset_state_id"],
                            "/source_fingerprint",
                            "source changed after the original intake receipt",
                        )
                    )
                return SourceAssetMutationResult(heads[root["source_asset_id"]], None)
        for head in heads.values():
            if (
                head["paper_id"] == paper_id
                and head["asset_role"] == asset_role
                and head["source_ref"] == source_ref
                and head["source_fingerprint"]["value"] == source_hash
                and head["availability"] == "available"
                and head["manifestation_status"] == "active"
            ):
                if paper_id is None:
                    raise ResearchKBError(
                        Diagnostic(
                            WRITE_CONFLICT,
                            "source-asset-state",
                            head["source_asset_state_id"],
                            "/job_id",
                            "an unassociated Source Asset remains owned by another intake Job",
                        )
                    )
                return SourceAssetMutationResult(head, None)

        source_asset_id = self.id_allocator(Namespace.SOURCE_ASSET)
        state_id = self.id_allocator(Namespace.SOURCE_ASSET_STATE)
        validate_id(source_asset_id, Namespace.SOURCE_ASSET)
        validate_id(state_id, Namespace.SOURCE_ASSET_STATE)
        if source_asset_id in {state["source_asset_id"] for state in states} or state_id in {
            state["source_asset_state_id"] for state in states
        }:
            raise ResearchKBError(
                Diagnostic(DUPLICATE_ID, "source-asset-state", state_id, "/source_asset_state_id", "allocated source asset ID is already in use")
            )
        now = timestamp(self.transactions.clock)
        state = {
            "schema_version": "1.0",
            "source_asset_state_id": state_id,
            "source_asset_id": source_asset_id,
            "workspace_id": self.layout.workspace_id,
            "revision": 1,
            "predecessor": None,
            "paper_id": paper_id,
            "asset_role": asset_role,
            "source_ref": source_ref,
            "source_fingerprint": {"algorithm": "sha256", "value": source_hash},
            "manifestation_id": f"sha256:{source_hash}",
            "manifestation_status": "active",
            "availability": "available",
            "reason": reason,
            "job_id": job_id,
            "actor": actor,
            "created_at": now,
            "updated_at": now,
        }
        if fixture_origin is not None:
            state["fixture_origin"] = fixture_origin
        return self._append(
            states,
            state,
            operation=transaction_operation,
            actor=actor,
            validate_source=validate_source,
        )

    def relink(
        self,
        *,
        source_asset_id: str,
        job_id: str,
        root_id: str,
        relative_path: str,
        expected_state_id: str,
        expected_state_digest: str,
        actor: str,
    ) -> SourceAssetMutationResult:
        require_job_authority(self.layout, job_id, "same_digest_relink")
        _require_actor(actor)
        head, states = self._require_head(source_asset_id, expected_state_id, expected_state_digest)
        observation = inspect_source_ref(
            self.layout,
            root_id=root_id,
            relative_path=relative_path,
        )
        source_ref, source, source_hash = _require_available_source(observation)
        active_hash = _active_fingerprint(states, source_asset_id)
        if source_hash != active_hash:
            raise ResearchKBError(
                Diagnostic(GROUNDING_MISMATCH, "source-asset-state", expected_state_id, "/source_fingerprint", "relink source digest does not match active manifestation")
            )
        if head["source_ref"] == source_ref.to_dict() and head["availability"] == "available":
            return SourceAssetMutationResult(head, None)
        state = self._successor(
            head,
            job_id=job_id,
            actor=actor,
            source_ref=source_ref.to_dict(),
            source_hash=source_hash,
            manifestation_status="active",
            availability="available",
            reason="same_digest_relink",
        )
        return self._append(
            states,
            state,
            operation="source_asset_relink",
            actor=actor,
            source=source,
            expected_source_hash=source_hash,
        )

    def observe(
        self,
        *,
        source_asset_id: str,
        job_id: str,
        expected_state_id: str,
        expected_state_digest: str,
        actor: str,
    ) -> SourceAssetMutationResult:
        require_job_authority(self.layout, job_id, "observe_source")
        _require_actor(actor)
        head, states = self._require_head(source_asset_id, expected_state_id, expected_state_digest)
        observation = inspect_source_ref(
            self.layout,
            root_id=head["source_ref"]["root_id"],
            relative_path=head["source_ref"]["relative_path"],
        )
        source = observation.path
        if observation.availability != "available":
            availability = (
                "relink_required"
                if observation.availability == "not_regular_file"
                else observation.availability
            )
            if head["availability"] == availability:
                return SourceAssetMutationResult(head, None)
            active_hash = _active_fingerprint(states, source_asset_id)
            state = self._successor(
                head,
                job_id=job_id,
                actor=actor,
                source_ref=head["source_ref"],
                source_hash=active_hash,
                manifestation_status="active",
                availability=availability,
                reason=f"source_{availability}",
            )
            return self._append(states, state, operation="source_asset_observe", actor=actor)
        source_hash = observation.live_sha256
        assert source_hash is not None
        if source_hash == head["source_fingerprint"]["value"] and head["availability"] == "available":
            return SourceAssetMutationResult(head, None)
        changed = source_hash != _active_fingerprint(states, source_asset_id)
        state = self._successor(
            head,
            job_id=job_id,
            actor=actor,
            source_ref=head["source_ref"],
            source_hash=source_hash,
            manifestation_status="change_candidate" if changed else "active",
            availability="available",
            reason="changed_bytes_observed" if changed else "source_available",
        )
        return self._append(
            states,
            state,
            operation="source_asset_observe",
            actor=actor,
            source=source,
            expected_source_hash=source_hash,
        )

    def _require_head(
        self,
        source_asset_id: str,
        expected_state_id: str,
        expected_state_digest: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        source_asset_id = validate_id(source_asset_id, Namespace.SOURCE_ASSET)
        expected_state_id = validate_id(expected_state_id, Namespace.SOURCE_ASSET_STATE)
        states = self._read_states()
        head = next(
            (item for item in current_source_asset_heads(states) if item["source_asset_id"] == source_asset_id),
            None,
        )
        if head is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "source-asset-state", source_asset_id, "/source_asset_id", "source asset does not exist")
            )
        if head["source_asset_state_id"] != expected_state_id or canonical_digest(head) != expected_state_digest:
            raise ResearchKBError(
                Diagnostic(WRITE_CONFLICT, "source-asset-state", expected_state_id, "/predecessor", "source asset current state changed before mutation")
            )
        return head, states

    def _read_states(self) -> list[dict[str, Any]]:
        states = read_jsonl(
            self.layout.source_assets_path,
            record_kind="source-asset-state",
            id_field="source_asset_state_id",
        )
        diagnostics = source_asset_chain_diagnostics(states)
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        return states

    def _successor(
        self,
        head: dict[str, Any],
        *,
        job_id: str,
        actor: str,
        source_ref: dict[str, str],
        source_hash: str,
        manifestation_status: str,
        availability: str,
        reason: str,
    ) -> dict[str, Any]:
        state_id = self.id_allocator(Namespace.SOURCE_ASSET_STATE)
        validate_id(state_id, Namespace.SOURCE_ASSET_STATE)
        now = timestamp(self.transactions.clock)
        state = {
            **{
                field: head[field]
                for field in (
                    "schema_version",
                    "source_asset_id",
                    "workspace_id",
                    "paper_id",
                    "asset_role",
                    "created_at",
                )
            },
            "source_asset_state_id": state_id,
            "revision": head["revision"] + 1,
            "predecessor": {
                "state_id": head["source_asset_state_id"],
                "state_digest": canonical_digest(head),
            },
            "source_ref": dict(source_ref),
            "source_fingerprint": {"algorithm": "sha256", "value": source_hash},
            "manifestation_id": f"sha256:{source_hash}",
            "manifestation_status": manifestation_status,
            "availability": availability,
            "reason": reason,
            "job_id": job_id,
            "actor": actor,
            "updated_at": now,
        }
        if "fixture_origin" in head:
            state["fixture_origin"] = head["fixture_origin"]
        return state

    def _append(
        self,
        states: list[dict[str, Any]],
        state: dict[str, Any],
        *,
        operation: str,
        actor: str,
        source: Path | None = None,
        expected_source_hash: str | None = None,
        validate_source: SourceValidator | None = None,
    ) -> SourceAssetMutationResult:
        diagnostics = validate_record("source-asset-state", state, actor="stored")
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        proposed = [*states, state]
        chain_diagnostics = source_asset_chain_diagnostics(proposed)
        if chain_diagnostics:
            raise ResearchKBError(chain_diagnostics[0])
        target = self.layout.source_assets_path
        before_sha256 = file_sha256(target)

        def validate_current_source() -> None:
            if validate_source is not None:
                validate_source()
                return
            if source is None:
                return
            observation = inspect_source_ref(
                self.layout,
                root_id=state["source_ref"]["root_id"],
                relative_path=state["source_ref"]["relative_path"],
            )
            if (
                observation.availability != "available"
                or observation.live_sha256 != expected_source_hash
            ):
                raise ResearchKBError(
                    Diagnostic(GROUNDING_MISMATCH, "source-asset-state", state["source_asset_state_id"], "/source_fingerprint", "source changed during source asset operation")
                )

        def validate_temp(path: Path) -> None:
            validate_current_source()
            temporary = read_jsonl(
                path,
                record_kind="source-asset-state",
                missing_ok=False,
                id_field="source_asset_state_id",
            )
            temporary_diagnostics = source_asset_chain_diagnostics(temporary)
            if temporary_diagnostics:
                raise ResearchKBError(temporary_diagnostics[0])
            entries = load_workspace_entries(
                self.layout,
                overrides={target: [("source-asset-state", item) for item in temporary]},
            )
            validate_workspace_entries(entries)

        transaction = self.transactions.promote_bytes(
            target=target,
            content=serialize_jsonl(proposed),
            target_store="source_assets",
            operation=operation,
            actor=actor,
            input_refs=([] if state["predecessor"] is None else [state["predecessor"]["state_id"]]),
            output_refs=[state["source_asset_id"], state["source_asset_state_id"]],
            validator=validate_temp,
            post_replace_validator=(
                validate_current_source
                if source is not None or validate_source is not None
                else None
            ),
            expected_before_sha256=before_sha256,
            job_id=state["job_id"],
        )
        return SourceAssetMutationResult(state, transaction)


def _active_fingerprint(states: list[dict[str, Any]], source_asset_id: str) -> str:
    active = next(
        (
            state
            for state in sorted(
                (item for item in states if item["source_asset_id"] == source_asset_id),
                key=lambda item: item["revision"],
                reverse=True,
            )
            if state["manifestation_status"] == "active"
        ),
        None,
    )
    if active is None:
        raise ResearchKBError(
            Diagnostic(GROUNDING_MISMATCH, "source-asset-state", source_asset_id, "/manifestation_status", "source asset has no active manifestation")
        )
    return active["source_fingerprint"]["value"]


def _require_actor(actor: str) -> None:
    if actor not in {"cli", "user"}:
        raise ResearchKBError(
            Diagnostic(INVALID_AUTHORITY, "source-asset-state", None, "/actor", "source asset mutation requires Core or user authority")
        )


def _require_available_source(observation: SourceRefObservation) -> tuple[Any, Path, str]:
    if observation.availability != "available" or observation.live_sha256 is None:
        code = (
            PATH_ESCAPE
            if observation.availability in {"relink_required", "not_regular_file"}
            else SCHEMA_VALIDATION_FAILED
        )
        raise ResearchKBError(
            Diagnostic(code, "source-asset-state", None, "/source_ref", f"source asset is not safely available: {observation.availability}")
        )
    return observation.source_ref, observation.path, observation.live_sha256


def _require_main_manifestation_match(
    *,
    asset_role: str,
    source_hash: str,
    paper: dict[str, Any],
    record_id: str | None,
) -> None:
    if (
        asset_role == "main_pdf"
        and source_hash != paper["source_fingerprint"]["value"]
    ):
        raise ResearchKBError(
            Diagnostic(
                GROUNDING_MISMATCH,
                "source-asset-state",
                record_id,
                "/source_fingerprint",
                "main PDF manifestation does not match the Registry paper source",
            )
        )


__all__ = ["SourceAssetMutationResult", "SourceAssetService"]
