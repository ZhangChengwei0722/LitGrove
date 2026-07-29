from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import INCOMPLETE_TRANSACTION, UNRESOLVED_REFERENCE, Diagnostic


TERMINAL_DISPOSITION_STATUSES = frozenset({"resolved", "false_positive", "superseded"})
INITIAL_DISPOSITION_STATUSES = frozenset(
    {"acknowledged", "accepted_risk", "remediation_planned", "false_positive"}
)
DISPOSITION_TRANSITIONS = {
    "acknowledged": frozenset(
        {"accepted_risk", "remediation_planned", "resolved", "false_positive", "superseded"}
    ),
    "accepted_risk": frozenset({"remediation_planned", "resolved", "superseded"}),
    "remediation_planned": frozenset(
        {"accepted_risk", "resolved", "false_positive", "superseded"}
    ),
}


def finding_digest(finding: Mapping[str, Any]) -> str:
    return canonical_digest(finding)


def guardian_disposition_diagnostics(
    dispositions: Iterable[dict[str, Any]],
    reports: Iterable[dict[str, Any]],
) -> list[Diagnostic]:
    disposition_list = list(dispositions)
    reports_by_id = {item["guardian_report_id"]: item for item in reports}
    by_finding: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    by_id: dict[str, dict[str, Any]] = {}
    diagnostics: list[Diagnostic] = []

    for item in disposition_list:
        disposition_id = item.get("disposition_id")
        if disposition_id in by_id:
            diagnostics.append(_diagnostic(item, "/disposition_id", "duplicate Guardian disposition ID"))
        if isinstance(disposition_id, str):
            by_id[disposition_id] = item
        key = (str(item.get("guardian_report_id", "")), int(item.get("finding_index", -1)))
        by_finding[key].append(item)

        report = reports_by_id.get(key[0])
        if report is None:
            diagnostics.append(
                Diagnostic(
                    UNRESOLVED_REFERENCE,
                    "guardian-finding-disposition",
                    disposition_id,
                    "/guardian_report_id",
                    "Guardian disposition references an unavailable report",
                )
            )
            continue
        findings = report.get("findings", [])
        if key[1] < 0 or key[1] >= len(findings):
            diagnostics.append(_diagnostic(item, "/finding_index", "Guardian disposition finding index is out of range"))
        elif item.get("finding_digest") != finding_digest(findings[key[1]]):
            diagnostics.append(_diagnostic(item, "/finding_digest", "Guardian disposition finding digest does not match the immutable report snapshot"))

    for records in by_finding.values():
        roots = [item for item in records if item.get("previous_disposition_id") is None]
        if len(roots) != 1:
            diagnostics.append(_diagnostic(records[0], "/previous_disposition_id", "Guardian finding must have exactly one disposition root"))
            continue
        ordered = [roots[0]]
        remaining = {item["disposition_id"]: item for item in records if item is not roots[0]}
        while remaining:
            successors = [
                item
                for item in remaining.values()
                if item.get("previous_disposition_id") == ordered[-1]["disposition_id"]
            ]
            if len(successors) != 1:
                diagnostics.append(_diagnostic(ordered[-1], "/previous_disposition_id", "Guardian disposition chain is forked or disconnected"))
                break
            successor = successors[0]
            ordered.append(successor)
            remaining.pop(successor["disposition_id"])

        if ordered[0].get("status") not in INITIAL_DISPOSITION_STATUSES:
            diagnostics.append(_diagnostic(ordered[0], "/status", "Guardian disposition root status is invalid"))
        for previous, current in zip(ordered, ordered[1:]):
            if previous.get("status") in TERMINAL_DISPOSITION_STATUSES:
                diagnostics.append(_diagnostic(current, "/status", "terminal Guardian disposition cannot have a successor"))
            elif current.get("status") not in DISPOSITION_TRANSITIONS.get(previous.get("status"), frozenset()):
                diagnostics.append(_diagnostic(current, "/status", "Guardian disposition transition is invalid"))

    return _deduplicate(diagnostics)


def current_guardian_dispositions(
    dispositions: Iterable[dict[str, Any]],
    reports: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    disposition_list = list(dispositions)
    diagnostics = guardian_disposition_diagnostics(disposition_list, reports)
    if diagnostics:
        from research_kb.errors import ResearchKBError

        raise ResearchKBError(diagnostics[0])
    predecessor_ids = {
        item["previous_disposition_id"]
        for item in disposition_list
        if item.get("previous_disposition_id") is not None
    }
    heads = [item for item in disposition_list if item["disposition_id"] not in predecessor_ids]
    return tuple(
        sorted(
            heads,
            key=lambda item: (item["guardian_report_id"], item["finding_index"]),
        )
    )


def _diagnostic(record: Mapping[str, Any], path: str, message: str) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION,
        "guardian-finding-disposition",
        record.get("disposition_id"),
        path,
        message,
    )


def _deduplicate(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    seen: set[tuple[str, str | None, str, str]] = set()
    result: list[Diagnostic] = []
    for item in diagnostics:
        key = (item.code, item.record_id, item.json_path, item.message)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


__all__ = [
    "DISPOSITION_TRANSITIONS",
    "INITIAL_DISPOSITION_STATUSES",
    "TERMINAL_DISPOSITION_STATUSES",
    "current_guardian_dispositions",
    "finding_digest",
    "guardian_disposition_diagnostics",
]
