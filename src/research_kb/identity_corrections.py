from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from research_kb.catalog.models import canonical_digest
from research_kb.errors import GROUNDING_MISMATCH, INCOMPLETE_TRANSACTION, Diagnostic, ResearchKBError


MERGE_OPERATIONS = {"confirmed_duplicate_merge", "paper_alias"}
LIFECYCLE_OPERATIONS = {"library_archive", "library_tombstone"}


def identity_correction_diagnostics(
    corrections: Iterable[dict[str, Any]],
    papers: Iterable[dict[str, Any]],
) -> list[Diagnostic]:
    correction_list = list(corrections)
    paper_ids = {paper.get("paper_id") for paper in papers if isinstance(paper.get("paper_id"), str)}
    diagnostics: list[Diagnostic] = []
    by_id: dict[str, dict[str, Any]] = {}
    for index, correction in enumerate(correction_list):
        correction_id = correction.get("correction_id")
        if isinstance(correction_id, str):
            if correction_id in by_id:
                diagnostics.append(_diagnostic(correction, "/correction_id", "duplicate identity correction ID"))
            by_id[correction_id] = correction
        previous = correction_list[index - 1] if index else None
        if previous is None:
            if correction.get("previous_correction_id") is not None or correction.get("previous_correction_digest") is not None:
                diagnostics.append(_diagnostic(correction, "/previous_correction_id", "identity correction root must not have a predecessor"))
        else:
            if correction.get("previous_correction_id") != previous.get("correction_id"):
                diagnostics.append(_diagnostic(correction, "/previous_correction_id", "identity correction predecessor ID does not match"))
            if correction.get("previous_correction_digest") != canonical_digest(previous):
                diagnostics.append(_diagnostic(correction, "/previous_correction_digest", "identity correction predecessor digest does not match"))
        subjects = correction.get("subject_paper_ids", [])
        if subjects != sorted(subjects):
            diagnostics.append(_diagnostic(correction, "/subject_paper_ids", "identity correction subjects must be sorted"))
        for paper_id in subjects:
            if paper_id not in paper_ids:
                diagnostics.append(_diagnostic(correction, "/subject_paper_ids", "identity correction references an unknown paper"))
        retained = correction.get("retained_paper_id")
        if retained is not None and retained not in paper_ids:
            diagnostics.append(_diagnostic(correction, "/retained_paper_id", "identity correction retained paper is unknown"))
        operation = correction.get("operation")
        supersedes = correction.get("supersedes_correction_id")
        if operation == "confirmed_duplicate_merge":
            if len(subjects) < 2 or retained not in subjects or supersedes is not None:
                diagnostics.append(_diagnostic(correction, "/operation", "duplicate merge requires two subjects and one retained subject"))
        elif operation == "paper_alias":
            if len(subjects) != 1 or retained is None or retained in subjects or supersedes is not None:
                diagnostics.append(_diagnostic(correction, "/operation", "paper alias requires one distinct source and retained target"))
        elif operation == "mistaken_merge_split":
            target = by_id.get(supersedes) if isinstance(supersedes, str) else None
            if retained is not None or target is None or target.get("operation") != "confirmed_duplicate_merge":
                diagnostics.append(_diagnostic(correction, "/supersedes_correction_id", "split must supersede an earlier duplicate merge"))
            elif not set(subjects).issubset(set(target.get("subject_paper_ids", [])) - {target.get("retained_paper_id")}):
                diagnostics.append(_diagnostic(correction, "/subject_paper_ids", "split subjects are outside the superseded merge"))
        elif operation in LIFECYCLE_OPERATIONS:
            if len(subjects) != 1 or retained is not None or supersedes is not None:
                diagnostics.append(_diagnostic(correction, "/operation", "library lifecycle correction requires one subject only"))

    if diagnostics:
        return _deduplicate(diagnostics)
    try:
        _project(paper_ids, correction_list)
    except ResearchKBError as error:
        diagnostics.append(error.diagnostic)
    return _deduplicate(diagnostics)


def project_registry_identity(
    papers: Iterable[dict[str, Any]],
    corrections: Iterable[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    paper_list = list(papers)
    correction_list = list(corrections)
    diagnostics = identity_correction_diagnostics(correction_list, paper_list)
    if diagnostics:
        raise ResearchKBError(diagnostics[0])
    paper_ids = {paper["paper_id"] for paper in paper_list}
    return _project(paper_ids, correction_list)


def _project(
    paper_ids: set[str],
    corrections: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    canonical = {paper_id: paper_id for paper_id in paper_ids}
    library = {paper_id: "active" for paper_id in paper_ids}
    for correction in corrections:
        operation = correction["operation"]
        subjects = correction["subject_paper_ids"]
        retained = correction["retained_paper_id"]
        if operation in MERGE_OPERATIONS:
            assert retained is not None
            for paper_id in subjects:
                canonical[paper_id] = retained
            if operation == "confirmed_duplicate_merge":
                canonical[retained] = retained
        elif operation == "mistaken_merge_split":
            for paper_id in subjects:
                canonical[paper_id] = paper_id
        elif operation == "library_archive":
            library[subjects[0]] = "archived"
        elif operation == "library_tombstone":
            library[subjects[0]] = "tombstoned"
        _require_acyclic(canonical, correction)
    return {
        paper_id: {
            "paper_id": paper_id,
            "canonical_paper_id": _resolve(canonical, paper_id),
            "library_status": library[paper_id],
        }
        for paper_id in sorted(paper_ids)
    }


def _require_acyclic(canonical: dict[str, str], correction: Mapping[str, Any]) -> None:
    for paper_id in canonical:
        seen: set[str] = set()
        current = paper_id
        while canonical[current] != current:
            if current in seen:
                raise ResearchKBError(
                    Diagnostic(
                        GROUNDING_MISMATCH,
                        "registry-identity-correction",
                        correction.get("correction_id") if isinstance(correction.get("correction_id"), str) else None,
                        "/retained_paper_id",
                        "Registry identity correction creates an alias cycle",
                    )
                )
            seen.add(current)
            current = canonical[current]


def _resolve(canonical: dict[str, str], paper_id: str) -> str:
    current = paper_id
    seen: set[str] = set()
    while canonical[current] != current:
        if current in seen:
            return current
        seen.add(current)
        current = canonical[current]
    return current


def _diagnostic(correction: Mapping[str, Any], path: str, message: str) -> Diagnostic:
    return Diagnostic(
        INCOMPLETE_TRANSACTION,
        "registry-identity-correction",
        correction.get("correction_id") if isinstance(correction.get("correction_id"), str) else None,
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


__all__ = ["identity_correction_diagnostics", "project_registry_identity"]
