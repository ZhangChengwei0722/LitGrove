from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.errors import SCHEMA_VALIDATION_FAILED, Diagnostic, ResearchKBError
from research_kb.exchange import BUNDLE_FORMAT, EXPORT_SELECTORS, ExchangeExportService
from research_kb.exchange_import import ExchangeImportService
from research_kb.services.workspace_session import WorkspaceSession


class ExchangeApplicationService:
    def limits(self, session: WorkspaceSession) -> dict[str, Any]:
        layout = _layout(session)
        return _response(
            {
                "bundle_format": BUNDLE_FORMAT,
                "selectors": list(EXPORT_SELECTORS),
                "source_inclusion_available": True,
                "import_available": True,
                "safe_reader_profile": ExchangeImportService(layout).limits()["safe_reader_profile"],
                "browser_paths_accepted": False,
                "external_records_are_local_facts": False,
            }
        )

    def preview_export(self, session: WorkspaceSession, request: Mapping[str, Any]) -> dict[str, Any]:
        result = ExchangeExportService(_layout(session)).preview(request)
        return _response({key: value for key, value in result.items() if key not in {"status", "interface_version", "persistent_writes", "canonical_scientific_write"}})

    def build_export(
        self,
        session: WorkspaceSession,
        request: Mapping[str, Any],
        *,
        target: Path,
        actor: str,
    ) -> dict[str, Any]:
        result = ExchangeExportService(_layout(session)).build(request, target=target, actor=actor)
        return _response({key: value for key, value in result.items() if key not in {"status", "interface_version", "persistent_writes", "canonical_scientific_write"}}, persistent_writes=1)

    def preview_import(self, session: WorkspaceSession, *, archive: Path) -> dict[str, Any]:
        result = ExchangeImportService(_layout(session)).preflight(archive)
        return _response({key: value for key, value in result.items() if key not in {"status", "interface_version", "persistent_writes", "canonical_scientific_write"}})

    def apply_import(
        self,
        session: WorkspaceSession,
        request: Mapping[str, Any],
        *,
        archive: Path,
        actor: str,
    ) -> dict[str, Any]:
        result = ExchangeImportService(_layout(session)).apply(archive, request, actor=actor)
        return _response(
            {key: value for key, value in result.items() if key not in {"status", "interface_version", "persistent_writes", "canonical_scientific_write"}},
            persistent_writes=result["persistent_writes"],
        )

    def list_imports(self, session: WorkspaceSession) -> dict[str, Any]:
        result = ExchangeImportService(_layout(session)).list_imports()
        return _response({"imports": result["imports"]})

    def show_import(self, session: WorkspaceSession, import_id: str) -> dict[str, Any]:
        result = ExchangeImportService(_layout(session)).show_import(import_id)
        return _response(
            {
                key: value
                for key, value in result.items()
                if key
                not in {
                    "status",
                    "interface_version",
                    "persistent_writes",
                    "canonical_scientific_write",
                }
            }
        )

    def recover_imports(self, session: WorkspaceSession, *, dry_run: bool) -> dict[str, Any]:
        result = ExchangeImportService(_layout(session)).recover(dry_run=dry_run)
        return _response(
            {key: value for key, value in result.items() if key != "status"},
            persistent_writes=0 if dry_run or not result["actions"] else 1,
        )


def _layout(session: WorkspaceSession):
    if not isinstance(session, WorkspaceSession):
        raise ResearchKBError(
            Diagnostic(
                SCHEMA_VALIDATION_FAILED,
                "exchange-application-request",
                None,
                "/session",
                "a Core-owned WorkspaceSession is required",
            )
        )
    return session._layout


def _response(payload: dict[str, Any], *, persistent_writes: int = 0) -> dict[str, Any]:
    return {
        "status": "success",
        "interface_version": "1.0",
        "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
        **payload,
        "persistent_writes": persistent_writes,
        "canonical_scientific_write": False,
    }


__all__ = ["ExchangeApplicationService"]
