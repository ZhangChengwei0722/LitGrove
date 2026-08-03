from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind
from research_kb.catalog.models import canonical_digest
from research_kb.contracts.validator import validate_record
from research_kb.errors import DUPLICATE_ID, INVALID_AUTHORITY, SCHEMA_VALIDATION_FAILED, UNRESOLVED_REFERENCE, WRITE_CONFLICT, Diagnostic, ResearchKBError
from research_kb.identifiers import Namespace, allocate_id, validate_id
from research_kb.identity_corrections import project_registry_identity
from research_kb.process_events import Clock, timestamp, utc_now
from research_kb.screening_bundles import active_screening_criteria, active_screening_decision, decision_freshness, screening_criteria_bundle_diagnostics, screening_decision_bundle_diagnostics
from research_kb.storage.json_io import file_sha256, read_json_document, serialize_json
from research_kb.storage.transactions import TransactionManager, TransactionResult
from research_kb.workspace import WorkspaceLayout


IdAllocator = Callable[[Namespace], str]
EntriesLoader = Callable[[WorkspaceLayout], list[BundleEntry]]


class QuestionScreeningService:
    def __init__(self, layout: WorkspaceLayout, *, transaction_manager: TransactionManager | None = None, id_allocator: IdAllocator = allocate_id, entries_loader: EntriesLoader = load_workspace_entries, clock: Clock = utc_now):
        self.layout = layout
        self.transactions = transaction_manager or TransactionManager(layout, clock=clock)
        self.id_allocator = id_allocator
        self.entries_loader = entries_loader
        self.clock = clock

    def promote_criteria(self, payload: Mapping[str, Any], *, criteria_id: str | None = None, approval: Mapping[str, Any], actor: str, expected_revision_id: str | None = None, fixture_origin: str | None = None) -> tuple[dict[str, Any], TransactionResult | None]:
        _require_approval(actor, approval)
        entries = self.entries_loader(self.layout)
        existing = None
        if criteria_id is not None:
            validate_id(criteria_id, Namespace.SCREENING_CRITERIA)
            existing = self._read_criteria(self.layout.screening_criteria_bundle_path(criteria_id))
            if existing is None:
                raise _error(UNRESOLVED_REFERENCE, "screening-criteria-bundle", criteria_id, "/criteria_id", "caller-supplied criteria ID does not exist")
        else:
            criteria_id = self.id_allocator(Namespace.SCREENING_CRITERIA)
        if existing is not None and expected_revision_id is None:
            raise _error(WRITE_CONFLICT, "screening-criteria-bundle", criteria_id, "/expected_revision_id", "current criteria head is required for revision")
        if expected_revision_id is not None and (existing or {}).get("active_revision_id") != expected_revision_id:
            raise _error(WRITE_CONFLICT, "screening-criteria-bundle", criteria_id, "/expected_revision_id", "criteria head changed before promotion")
        current = None if existing is None else active_screening_criteria(existing)
        question_id = payload.get("question_id", (current or {}).get("question_id"))
        if not isinstance(question_id, str):
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-criteria-bundle", criteria_id, "/question_id", "question_id is required")
        validate_id(question_id, Namespace.QUESTION)
        self._require_question(question_id, entries)
        if existing is not None and question_id != existing["question_id"]:
            raise _error(INVALID_AUTHORITY, "screening-criteria-bundle", criteria_id, "/question_id", "criteria cannot change Question owner")
        criteria = {
            "criteria_id": criteria_id,
            "question_id": question_id,
            "title": _bounded_text(payload.get("title", (current or {}).get("title")), "/title", 200, required=True),
            "scope": _bounded_text(payload.get("scope", (current or {}).get("scope")), "/scope", 4000, required=True),
            "inclusion_criteria": self._normalize_items(payload.get("inclusion_criteria", (current or {}).get("inclusion_criteria", [])), current, "inclusion_criteria"),
            "exclusion_criteria": self._normalize_items(payload.get("exclusion_criteria", (current or {}).get("exclusion_criteria", [])), current, "exclusion_criteria"),
            "notes": _bounded_text(payload.get("notes", (current or {}).get("notes", "")), "/notes", 4000),
            "status": payload.get("status", (current or {}).get("status", "active")),
        }
        if criteria["status"] not in {"active", "archived"} or not criteria["inclusion_criteria"] and not criteria["exclusion_criteria"]:
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-criteria-bundle", criteria_id, "/criteria", "criteria requires a supported status and at least one criterion")
        self._require_active_criteria_owner(question_id, criteria_id, criteria["status"])
        digest = canonical_digest(criteria)
        if existing is not None and existing["revisions"][-1]["content_digest"] == digest:
            return existing, None
        now = timestamp(self.clock)
        previous = None if existing is None else existing["revisions"][-1]
        revision = {
            "revision_id": self.id_allocator(Namespace.SCREENING_CRITERIA_REVISION),
            "revision_number": 1 if previous is None else len(existing["revisions"]) + 1,
            "predecessor": None if previous is None else {"revision_id": previous["revision_id"], "revision_digest": canonical_digest(previous)},
            "content_digest": digest,
            "approval": dict(approval),
            "criteria": criteria,
            "created_at": now,
        }
        bundle = {"schema_version": "1.0", "criteria_id": criteria_id, "question_id": question_id, "active_revision_id": revision["revision_id"], "revisions": [revision] if existing is None else [*deepcopy(existing["revisions"]), revision], "created_at": now if existing is None else existing["created_at"], "updated_at": now}
        if fixture_origin is not None:
            bundle["fixture_origin"] = fixture_origin
        transaction = self._write_bundle(self.layout.screening_criteria_bundle_path(criteria_id), bundle, "screening-criteria-bundle", "organization_screening_criteria", "criteria_id", screening_criteria_bundle_diagnostics, locked_precondition=lambda: self._require_active_criteria_owner(question_id, criteria_id, criteria["status"]))
        return bundle, transaction

    def promote_decision(self, payload: Mapping[str, Any], *, decision_id: str | None = None, approval: Mapping[str, Any], actor: str, expected_revision_id: str | None = None, fixture_origin: str | None = None) -> tuple[dict[str, Any], TransactionResult | None]:
        _require_approval(actor, approval)
        entries = self.entries_loader(self.layout)
        existing = None
        if decision_id is not None:
            validate_id(decision_id, Namespace.SCREENING_DECISION)
            existing = self._read_decision(self.layout.screening_decision_bundle_path(decision_id))
            if existing is None:
                raise _error(UNRESOLVED_REFERENCE, "screening-decision-bundle", decision_id, "/decision_id", "caller-supplied decision ID does not exist")
        else:
            decision_id = self.id_allocator(Namespace.SCREENING_DECISION)
        current = None if existing is None else active_screening_decision(existing)
        if existing is not None and expected_revision_id is None:
            raise _error(WRITE_CONFLICT, "screening-decision-bundle", decision_id, "/expected_revision_id", "current decision head is required for revision")
        if expected_revision_id is not None and (existing or {}).get("active_revision_id") != expected_revision_id:
            raise _error(WRITE_CONFLICT, "screening-decision-bundle", decision_id, "/expected_revision_id", "decision head changed before promotion")
        question_id = payload.get("question_id", (current or {}).get("question_id"))
        paper_id = payload.get("paper_id", (current or {}).get("paper_id"))
        if not isinstance(question_id, str) or not isinstance(paper_id, str):
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-decision-bundle", decision_id, "", "question_id and paper_id are required")
        validate_id(question_id, Namespace.QUESTION)
        validate_id(paper_id, Namespace.PAPER)
        if existing is not None and (question_id, paper_id) != (existing["question_id"], existing["paper_id"]):
            raise _error(INVALID_AUTHORITY, "screening-decision-bundle", decision_id, "", "decision cannot change Question-Paper owner")
        self._require_question(question_id, entries)
        self._require_paper(paper_id, entries)
        criteria_bundle = self._active_criteria(question_id)
        if criteria_bundle is None:
            raise _error(UNRESOLVED_REFERENCE, "screening-decision-bundle", decision_id, "/criteria_revision_id", "Question has no active screening criteria")
        criteria_revision = criteria_bundle["revisions"][-1]
        if payload.get("criteria_revision_id") != criteria_revision["revision_id"] or payload.get("criteria_digest") != criteria_revision["content_digest"]:
            raise _error(WRITE_CONFLICT, "screening-decision-bundle", decision_id, "/criteria_revision_id", "decision must bind the current criteria revision and digest")
        outcome = payload.get("outcome")
        basis_scope = payload.get("basis_scope")
        if outcome not in {"included", "excluded"} or basis_scope not in {"metadata", "available_abstract", "paper_card", "user_full_text_review", "mixed"}:
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-decision-bundle", decision_id, "/decision", "unsupported outcome or basis scope")
        dispositions = self._normalize_dispositions(payload.get("criterion_dispositions"), criteria_revision["criteria"])
        decision = {
            "decision_id": decision_id,
            "question_id": question_id,
            "paper_id": paper_id,
            "outcome": outcome,
            "criteria_revision_id": criteria_revision["revision_id"],
            "criteria_digest": criteria_revision["content_digest"],
            "criterion_dispositions": dispositions,
            "basis_scope": basis_scope,
            "rationale": _bounded_text(payload.get("rationale"), "/rationale", 4000, required=True),
            "known_limitations": _string_list(payload.get("known_limitations", []), "/known_limitations", 50, 1000),
        }
        self._require_pair_owner(question_id, paper_id, decision_id)
        digest = canonical_digest(decision)
        if existing is not None and existing["revisions"][-1]["content_digest"] == digest:
            return existing, None
        now = timestamp(self.clock)
        previous = None if existing is None else existing["revisions"][-1]
        revision = {"revision_id": self.id_allocator(Namespace.SCREENING_DECISION_REVISION), "revision_number": 1 if previous is None else len(existing["revisions"]) + 1, "predecessor": None if previous is None else {"revision_id": previous["revision_id"], "revision_digest": canonical_digest(previous)}, "content_digest": digest, "approval": dict(approval), "decision": decision, "created_at": now}
        bundle = {"schema_version": "1.0", "decision_id": decision_id, "question_id": question_id, "paper_id": paper_id, "active_revision_id": revision["revision_id"], "revisions": [revision] if existing is None else [*deepcopy(existing["revisions"]), revision], "created_at": now if existing is None else existing["created_at"], "updated_at": now}
        if fixture_origin is not None:
            bundle["fixture_origin"] = fixture_origin
        transaction = self._write_bundle(
            self.layout.screening_decision_bundle_path(decision_id),
            bundle,
            "screening-decision-bundle",
            "organization_screening_decisions",
            "decision_id",
            screening_decision_bundle_diagnostics,
            dependency_refs=(criteria_revision["revision_id"],),
            locked_precondition=lambda: self._require_decision_commit_preconditions(
                question_id,
                paper_id,
                decision_id,
                criteria_revision["revision_id"],
                criteria_revision["content_digest"],
            ),
        )
        return bundle, transaction

    def list_criteria(self, *, question_id: str | None = None, include_archived: bool = False) -> list[dict[str, Any]]:
        result = []
        root = self.layout.knowledge_root / "organization" / "screening_criteria" / "by_id"
        for path in sorted(root.glob("*.screening-criteria-bundle.json")) if root.exists() else []:
            bundle = self._read_criteria(path)
            criteria = active_screening_criteria(bundle)
            if question_id is not None and bundle["question_id"] != question_id:
                continue
            if include_archived or criteria["status"] == "active":
                result.append({**criteria, "revision_id": bundle["active_revision_id"], "criteria_digest": bundle["revisions"][-1]["content_digest"]})
        return sorted(result, key=lambda item: item["criteria_id"])

    def list_decisions(self, *, question_id: str | None = None, paper_id: str | None = None) -> list[dict[str, Any]]:
        entries = self._entries_with_screening()
        result = []
        root = self.layout.knowledge_root / "organization" / "screening_decisions" / "by_id"
        for path in sorted(root.glob("*.screening-decision-bundle.json")) if root.exists() else []:
            bundle = self._read_decision(path)
            if question_id is not None and bundle["question_id"] != question_id or paper_id is not None and bundle["paper_id"] != paper_id:
                continue
            result.append({**active_screening_decision(bundle), "revision_id": bundle["active_revision_id"], "freshness": decision_freshness(bundle, entries)})
        return sorted(result, key=lambda item: item["decision_id"])

    def read_criteria(self, criteria_id: str) -> dict[str, Any]:
        validate_id(criteria_id, Namespace.SCREENING_CRITERIA)
        bundle = self._read_criteria(self.layout.screening_criteria_bundle_path(criteria_id))
        if bundle is None:
            raise _error(UNRESOLVED_REFERENCE, "screening-criteria-bundle", criteria_id, "", "criteria does not exist")
        return {**active_screening_criteria(bundle), "revision_id": bundle["active_revision_id"], "criteria_digest": bundle["revisions"][-1]["content_digest"]}

    def read_decision(self, decision_id: str) -> dict[str, Any]:
        validate_id(decision_id, Namespace.SCREENING_DECISION)
        bundle = self._read_decision(self.layout.screening_decision_bundle_path(decision_id))
        if bundle is None:
            raise _error(UNRESOLVED_REFERENCE, "screening-decision-bundle", decision_id, "", "decision does not exist")
        return {**active_screening_decision(bundle), "revision_id": bundle["active_revision_id"], "freshness": decision_freshness(bundle, self._entries_with_screening())}

    def _entries_with_screening(self) -> list[BundleEntry]:
        entries = [
            (kind, record)
            for kind, record in self.entries_loader(self.layout)
            if kind not in {"screening-criteria-bundle", "screening-decision-bundle"}
        ]
        for root, pattern, kind, reader in (
            (self.layout.knowledge_root / "organization" / "screening_criteria" / "by_id", "*.screening-criteria-bundle.json", "screening-criteria-bundle", self._read_criteria),
            (self.layout.knowledge_root / "organization" / "screening_decisions" / "by_id", "*.screening-decision-bundle.json", "screening-decision-bundle", self._read_decision),
        ):
            for path in sorted(root.glob(pattern)) if root.exists() else []:
                record = reader(path)
                if record is not None:
                    entries.append((kind, record))
        return entries

    def _normalize_items(self, value: object, current: Mapping[str, Any] | None, field: str) -> list[dict[str, str]]:
        if not isinstance(value, list) or len(value) > 100:
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-criteria-bundle", None, f"/{field}", "criteria items must be an array with at most 100 entries")
        previous = {item["criterion_id"]: item for item in (current or {}).get(field, [])}
        result = []
        seen: set[str] = set()
        for index, source in enumerate(value):
            if isinstance(source, str):
                item_id = self.id_allocator(Namespace.SCREENING_CRITERION)
                text = source
            elif isinstance(source, Mapping) and set(source) <= {"criterion_id", "text"} and "text" in source:
                supplied_id = source.get("criterion_id")
                if current is None and supplied_id is not None:
                    raise _error(INVALID_AUTHORITY, "screening-criteria-bundle", None, f"/{field}/{index}/criterion_id", "criterion IDs are Core-owned")
                item_id = supplied_id or self.id_allocator(Namespace.SCREENING_CRITERION)
                validate_id(item_id, Namespace.SCREENING_CRITERION)
                if item_id not in previous and current is not None:
                    raise _error(INVALID_AUTHORITY, "screening-criteria-bundle", None, f"/{field}/{index}/criterion_id", "new criterion IDs are Core-owned")
                text = source["text"]
            else:
                raise _error(SCHEMA_VALIDATION_FAILED, "screening-criteria-bundle", None, f"/{field}/{index}", "criterion must be text or a retained criterion object")
            text = _bounded_text(text, f"/{field}/{index}/text", 1000, required=True)
            if item_id in seen:
                raise _error(DUPLICATE_ID, "screening-criteria-bundle", item_id, f"/{field}", "duplicate criterion ID")
            seen.add(item_id)
            result.append({"criterion_id": item_id, "text": text})
        return result

    @staticmethod
    def _normalize_dispositions(value: object, criteria: Mapping[str, Any]) -> list[dict[str, str]]:
        if not isinstance(value, list):
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-decision-bundle", None, "/criterion_dispositions", "criterion dispositions must be an array")
        expected = {item["criterion_id"] for field in ("inclusion_criteria", "exclusion_criteria") for item in criteria[field]}
        result = []
        seen: set[str] = set()
        for index, item in enumerate(value):
            if not isinstance(item, Mapping) or set(item) != {"criterion_id", "disposition", "rationale"}:
                raise _error(SCHEMA_VALIDATION_FAILED, "screening-decision-bundle", None, f"/criterion_dispositions/{index}", "disposition fields do not match the closed contract")
            criterion_id = item["criterion_id"]
            if criterion_id not in expected or criterion_id in seen or item["disposition"] not in {"met", "not_met", "not_applicable", "uncertain"}:
                raise _error(SCHEMA_VALIDATION_FAILED, "screening-decision-bundle", None, f"/criterion_dispositions/{index}", "disposition references an unavailable criterion or unsupported state")
            seen.add(criterion_id)
            result.append({"criterion_id": criterion_id, "disposition": item["disposition"], "rationale": _bounded_text(item["rationale"], f"/criterion_dispositions/{index}/rationale", 2000, required=True)})
        if seen != expected:
            raise _error(SCHEMA_VALIDATION_FAILED, "screening-decision-bundle", None, "/criterion_dispositions", "one disposition is required for every criterion")
        return result

    def _require_question(self, question_id: str, entries: list[BundleEntry]) -> None:
        if not any(item.get("question_id") == question_id for kind in ("question-mapping", "question-revision-bundle") for item in records_of_kind(entries, kind)):
            raise _error(UNRESOLVED_REFERENCE, "screening", question_id, "/question_id", "Question does not exist")

    @staticmethod
    def _require_paper(paper_id: str, entries: list[BundleEntry]) -> None:
        papers = records_of_kind(entries, "registry-paper")
        corrections = [record for kind, record in entries if kind == "registry-identity-correction"]
        paper = project_registry_identity(papers, corrections).get(paper_id)
        if paper is None or paper.get("canonical_paper_id") != paper_id or paper.get("library_status") != "active":
            raise _error(UNRESOLVED_REFERENCE, "screening", paper_id, "/paper_id", "Paper is unavailable or no longer canonical")

    def _active_criteria(self, question_id: str) -> dict[str, Any] | None:
        matches = []
        root = self.layout.knowledge_root / "organization" / "screening_criteria" / "by_id"
        for path in sorted(root.glob("*.screening-criteria-bundle.json")) if root.exists() else []:
            bundle = self._read_criteria(path)
            if bundle["question_id"] == question_id and active_screening_criteria(bundle)["status"] == "active":
                matches.append(bundle)
        if len(matches) > 1:
            raise _error(DUPLICATE_ID, "screening-criteria-bundle", None, "/question_id", "multiple active criteria sets govern one Question")
        return None if not matches else matches[0]

    def _require_active_criteria_owner(self, question_id: str, criteria_id: str, status: str) -> None:
        if status != "active":
            return
        owner = self._active_criteria(question_id)
        if owner is not None and owner["criteria_id"] != criteria_id:
            raise _error(DUPLICATE_ID, "screening-criteria-bundle", criteria_id, "/question_id", "another active criteria set already governs this Question")

    def _require_pair_owner(self, question_id: str, paper_id: str, decision_id: str) -> None:
        root = self.layout.knowledge_root / "organization" / "screening_decisions" / "by_id"
        for path in sorted(root.glob("*.screening-decision-bundle.json")) if root.exists() else []:
            bundle = self._read_decision(path)
            if (bundle["question_id"], bundle["paper_id"]) == (question_id, paper_id) and bundle["decision_id"] != decision_id:
                raise _error(DUPLICATE_ID, "screening-decision-bundle", decision_id, "", "another decision already owns this Question-Paper pair")

    def _require_decision_commit_preconditions(self, question_id: str, paper_id: str, decision_id: str, criteria_revision_id: str, criteria_digest: str) -> None:
        self._require_pair_owner(question_id, paper_id, decision_id)
        entries = self.entries_loader(self.layout)
        self._require_question(question_id, entries)
        self._require_paper(paper_id, entries)
        criteria = self._active_criteria(question_id)
        if criteria is None or criteria["active_revision_id"] != criteria_revision_id or criteria["revisions"][-1]["content_digest"] != criteria_digest:
            raise _error(WRITE_CONFLICT, "screening-decision-bundle", decision_id, "/criteria_revision_id", "criteria changed before decision commit")

    def _read_criteria(self, path: Path) -> dict[str, Any] | None:
        return self._read_bundle(path, "screening-criteria-bundle", screening_criteria_bundle_diagnostics)

    def _read_decision(self, path: Path) -> dict[str, Any] | None:
        return self._read_bundle(path, "screening-decision-bundle", screening_decision_bundle_diagnostics)

    @staticmethod
    def _read_bundle(path: Path, kind: str, diagnostics_fn) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        bundle = read_json_document(path, record_kind=kind)
        diagnostics = [*validate_record(kind, bundle, actor="stored"), *diagnostics_fn(bundle)]
        if diagnostics:
            raise ResearchKBError(diagnostics[0])
        return bundle

    def _write_bundle(
        self,
        target: Path,
        bundle: dict[str, Any],
        kind: str,
        store: str,
        id_field: str,
        diagnostics_fn,
        *,
        dependency_refs: tuple[str, ...] = (),
        locked_precondition=None,
    ) -> TransactionResult:
        def validate_temp(path: Path) -> None:
            candidate = read_json_document(path, record_kind=kind)
            diagnostics = [*validate_record(kind, candidate, actor="stored"), *diagnostics_fn(candidate)]
            if diagnostics:
                raise ResearchKBError(diagnostics[0])
        input_refs = [
            value
            for value in (bundle.get("question_id"), bundle.get("paper_id"), *dependency_refs)
            if isinstance(value, str)
        ]
        return self.transactions.promote_bytes(target=target, content=serialize_json(bundle), target_store=store, operation=f"{kind.replace('-', '_')}_commit", actor="user", input_refs=input_refs, output_refs=[bundle["active_revision_id"]], validator=validate_temp, locked_precondition=locked_precondition, expected_before_sha256=file_sha256(target))


def _bounded_text(value: object, path: str, limit: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or required and not value.strip():
        raise _error(SCHEMA_VALIDATION_FAILED, "screening", None, path, f"text must be {'non-empty and ' if required else ''}at most {limit} characters")
    return value.strip()


def _string_list(value: object, path: str, count: int, limit: int) -> list[str]:
    if not isinstance(value, list) or len(value) > count:
        raise _error(SCHEMA_VALIDATION_FAILED, "screening", None, path, "value exceeds the closed list budget")
    return [_bounded_text(item, f"{path}/{index}", limit, required=True) for index, item in enumerate(value)]


def _require_approval(actor: str, approval: Mapping[str, Any]) -> None:
    direct = {"receipt_id", "approved_by", "approved_at", "origin"}
    agent = {"approved_by", "approved_at", "origin", "task_id", "task_result_digest"}
    valid_direct = set(approval) == direct and approval.get("origin") == "user_authored"
    valid_agent = set(approval) == agent and approval.get("origin") == "user_approved_agent_proposal"
    if actor != "user" or approval.get("approved_by") != "user" or not (valid_direct or valid_agent) or not all(isinstance(value, str) and value for value in approval.values()):
        raise _error(INVALID_AUTHORITY, "screening", None, "/approval", "screening mutation requires explicit user approval provenance")


def _error(code: str, kind: str, record_id: str | None, path: str, message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(code, kind, record_id, path, message))


__all__ = ["QuestionScreeningService"]
