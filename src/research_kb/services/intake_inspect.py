from __future__ import annotations

from pathlib import Path
from typing import Any

from research_kb.bundle import load_workspace_entries, records_of_kind, validate_workspace_entries
from research_kb.errors import GROUNDING_MISMATCH, PATH_ESCAPE, SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.storage.json_io import file_sha256
from research_kb.workspace import WorkspaceLayout


class IntakeInspectService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def inspect(self, *, source: Path) -> dict[str, Any]:
        if not source.is_absolute():
            raise _path_error("source path must be absolute")

        entries = load_workspace_entries(self.layout)
        validate_workspace_entries(entries)
        resolved_source = _resolve_regular_file(source)
        root_id, relative_path = self._project_source_ref(resolved_source)

        source_hash = file_sha256(resolved_source)
        if source_hash is None:
            raise _source_error("source asset is missing or is not a regular file")

        exact_matches = sorted(
            (
                record
                for record in records_of_kind(entries, "registry-paper")
                if record["source_ref"] == {
                    "root_id": root_id,
                    "relative_path": relative_path,
                }
            ),
            key=lambda record: record["paper_id"],
        )
        registration = _registration_projection(exact_matches, source_hash)
        profiles = records_of_kind(entries, "domain-profile")
        if len(profiles) != 1:
            raise _source_error("workspace does not contain exactly one domain profile")
        profile = profiles[0]

        if file_sha256(resolved_source) != source_hash:
            raise ResearchKBError(
                Diagnostic(
                    GROUNDING_MISMATCH,
                    "source-ref",
                    None,
                    "/source",
                    "source changed during intake inspection",
                )
            )

        return {
            "status": "success",
            "interface_version": "1.0",
            "workspace_id": self.layout.workspace_id,
            "source": {
                "root_id": root_id,
                "relative_path": relative_path,
                "fingerprint_algorithm": "sha256",
            },
            "registration": registration,
            "domain_profile": {
                "id": profile["domain_profile"]["id"],
                "version": profile["domain_profile"]["version"],
                "paper_card_sections": [
                    {
                        "section_id": section["section_id"],
                        "label": section["label"],
                    }
                    for section in profile["paper_card_sections"]
                ],
            },
        }

    def _project_source_ref(self, source: Path) -> tuple[str, str]:
        containing_roots = [
            (root_id, root.resolve())
            for root_id, root in self.layout.source_roots.items()
            if source.is_relative_to(root.resolve())
        ]
        if len(containing_roots) != 1:
            raise _path_error("source must belong to exactly one declared source root")

        root_id, root = containing_roots[0]
        relative_path = source.relative_to(root).as_posix()
        _, round_trip = self.layout.resolve_source(root_id, relative_path)
        if round_trip != source:
            raise _path_error("source path could not be projected safely")
        return root_id, relative_path


def _resolve_regular_file(source: Path) -> Path:
    try:
        resolved = source.resolve(strict=True)
    except OSError as error:
        raise _source_error("source asset is missing or is not a regular file") from error
    if not resolved.is_file():
        raise _source_error("source asset is missing or is not a regular file")
    return resolved


def _registration_projection(
    exact_matches: list[dict[str, Any]],
    source_hash: str,
) -> dict[str, Any]:
    paper_ids = [record["paper_id"] for record in exact_matches]
    if not exact_matches:
        state = "unregistered"
    elif len(exact_matches) > 1:
        state = "ambiguous"
    elif exact_matches[0]["source_fingerprint"]["value"] == source_hash:
        state = "registered_current"
    else:
        state = "registered_stale"
    return {"state": state, "paper_ids": paper_ids}


def _path_error(message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(PATH_ESCAPE, "source-ref", None, "/source", message))


def _source_error(message: str) -> ResearchKBError:
    return ResearchKBError(Diagnostic(SCHEMA_VALIDATION_FAILED, "source-ref", None, "/source", message))


__all__ = ["IntakeInspectService"]
