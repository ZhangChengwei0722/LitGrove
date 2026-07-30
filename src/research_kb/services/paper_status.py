from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.bundle import BundleEntry, load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.contracts.validator import validate_record
from research_kb.errors import GROUNDING_MISMATCH, UNRESOLVED_REFERENCE, Diagnostic, ResearchKBError
from research_kb.guardian import GuardianService
from research_kb.identifiers import Namespace, validate_id
from research_kb.services.question_mapping import mapping_freshness_diagnostics
from research_kb.source_resolution import observe_paper_source
from research_kb.storage.json_io import file_sha256, read_json_document
from research_kb.workspace import WorkspaceLayout


GROUNDING_STATUSES = ("grounded", "revised", "interpretive", "background_only", "needs_resolution")
QUEUE_RESOLUTION_STATUSES = ("needs_resolution", "resolved_by_narrowing", "needs_source_reopen")


class PaperStatusService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def show(self, *, paper_id: str) -> dict[str, Any]:
        validate_id(paper_id, Namespace.PAPER)
        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        paper = next(
            (item for item in records_of_kind(entries, "registry-paper") if item["paper_id"] == paper_id),
            None,
        )
        if paper is None:
            raise ResearchKBError(
                Diagnostic(UNRESOLVED_REFERENCE, "registry-paper", paper_id, "/paper_id", "paper is not registered")
            )

        source_before = self._observe_source(paper)
        pages = sorted(
            (item for item in records_of_kind(entries, "parsed-page") if item["paper_id"] == paper_id),
            key=lambda item: item["pdf_page"],
        )
        card = next(
            (item for item in records_of_kind(entries, "paper-card") if item["paper_id"] == paper_id),
            None,
        )
        evidence = [item for item in records_of_kind(entries, "evidence") if item["paper_id"] == paper_id]
        queue = [item for item in records_of_kind(entries, "review-queue") if item["paper_id"] == paper_id]
        mappings = _linked_mappings(entries, paper_id)
        guardian = GuardianService(self.layout).check(write_report=False).report
        incomplete_count, needs_resolution_count = _transaction_counts(self.layout)
        reachable = _reachable_ids(entries, paper_id)
        paper_findings = [
            finding for finding in guardian["findings"] if finding.get("record_ref") in reachable
        ]
        workspace_findings = [
            finding for finding in guardian["findings"] if finding.get("record_ref") not in reachable
        ]

        result = {
            "status": "success",
            "interface_version": "1.0",
            "paper_id": paper_id,
            "source": {
                "registered": True,
                "state": source_before[0],
                "fingerprint_algorithm": paper["source_fingerprint"]["algorithm"],
            },
            "parse": _parse_projection(pages, source_before[0]),
            "paper_card": _card_projection(card),
            "evidence": {"count": len(evidence)},
            "review_queue": _queue_projection(queue),
            "question_mappings": {
                "linked_count": len(mappings),
                "items": [
                    {
                        "question_id": mapping["question_id"],
                        "mapping_status": mapping["mapping_status"],
                        "freshness": (
                            "stale" if mapping_freshness_diagnostics(mapping, entries) else "current"
                        ),
                    }
                    for mapping in mappings
                ],
            },
            "integrity": {
                "guardian_status": guardian["status"],
                "paper_finding_codes": sorted({finding["code"] for finding in paper_findings}),
                "workspace_finding_count": len(workspace_findings),
                "incomplete_transaction_count": incomplete_count,
                "needs_resolution_transaction_count": needs_resolution_count,
                "mutation_safe": (
                    source_before[0] == "current"
                    and guardian["status"] != "failure"
                    and incomplete_count == 0
                    and needs_resolution_count == 0
                ),
            },
        }
        if self._observe_source(paper) != source_before:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "registry-paper",
                    paper_id,
                    "/source_fingerprint",
                    "registered source changed during status projection",
                )
            )
        return result

    def _observe_source(self, paper: dict[str, Any]) -> tuple[str, str | None]:
        entries = load_workspace_entries(self.layout)
        observation = observe_paper_source(self.layout, entries, paper)
        return observation.state, observation.live_sha256


def _parse_projection(pages: list[dict[str, Any]], source_state: str) -> dict[str, Any]:
    if not pages:
        return {
            "state": "absent",
            "parse_run_id": None,
            "adapter": None,
            "version": None,
            "page_count": 0,
        }
    return {
        "state": "current" if source_state == "current" else "stale_source",
        "parse_run_id": pages[0]["parse_run_id"],
        "adapter": pages[0]["parser"]["adapter"],
        "version": pages[0]["parser"]["version"],
        "page_count": len(pages),
    }


def _card_projection(card: dict[str, Any] | None) -> dict[str, Any]:
    counts = {status: 0 for status in GROUNDING_STATUSES}
    if card is None:
        return {
            "present": False,
            "card_status": None,
            "review_status": None,
            "unit_count": 0,
            "grounding_counts": counts,
        }
    units = [unit for section in card["sections"] for unit in section["units"]]
    for unit in units:
        counts[unit["grounding_status"]] += 1
    return {
        "present": True,
        "card_status": card["card_status"],
        "review_status": card["review_status"],
        "unit_count": len(units),
        "grounding_counts": counts,
    }


def _queue_projection(queue: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in QUEUE_RESOLUTION_STATUSES}
    for item in queue:
        counts[item["resolution_status"]] += 1
    return {"count": len(queue), "resolution_counts": counts}


def _linked_mappings(entries: list[BundleEntry], paper_id: str) -> list[dict[str, Any]]:
    return sorted(
        (
            mapping
            for mapping in records_of_kind(entries, "question-mapping")
            if any(link["paper_id"] == paper_id for link in mapping["paper_links"])
        ),
        key=lambda item: item["question_id"],
    )


def _transaction_counts(layout: WorkspaceLayout) -> tuple[int, int]:
    incomplete = 0
    needs_resolution = 0
    if not layout.transactions_root.exists():
        return incomplete, needs_resolution
    for path in sorted(layout.transactions_root.glob("*.json"), key=lambda item: item.name):
        try:
            journal = read_json_document(path, record_kind="transaction-journal")
            diagnostics = validate_record("transaction-journal", journal, actor="stored")
        except ResearchKBError:
            incomplete += 1
            continue
        if diagnostics:
            incomplete += 1
        elif journal["phase"] == "needs_resolution":
            needs_resolution += 1
        elif journal["phase"] != "complete":
            incomplete += 1
    return incomplete, needs_resolution


def _reachable_ids(entries: list[BundleEntry], paper_id: str) -> set[str]:
    reachable = {paper_id}
    cards = [item for item in records_of_kind(entries, "paper-card") if item["paper_id"] == paper_id]
    for page in records_of_kind(entries, "parsed-page"):
        if page["paper_id"] == paper_id:
            reachable.add(page["parse_run_id"])
    for card in cards:
        for section in card["sections"]:
            reachable.update(unit["unit_id"] for unit in section["units"])
    reachable.update(
        item["evidence_id"] for item in records_of_kind(entries, "evidence") if item["paper_id"] == paper_id
    )
    reachable.update(
        item["queue_id"] for item in records_of_kind(entries, "review-queue") if item["paper_id"] == paper_id
    )
    for mapping in _linked_mappings(entries, paper_id):
        reachable.add(mapping["question_id"])
        reachable.update(
            link["question_link_id"] for link in mapping["paper_links"] if link["paper_id"] == paper_id
        )

    events = records_of_kind(entries, "process-event")
    changed = True
    while changed:
        changed = False
        for event in events:
            if event["event_id"] in reachable:
                continue
            if reachable.intersection((*event["input_refs"], *event["output_refs"])):
                reachable.add(event["event_id"])
                changed = True
    return reachable


__all__ = ["PaperStatusService"]
