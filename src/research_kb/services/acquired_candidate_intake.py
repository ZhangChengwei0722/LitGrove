from __future__ import annotations

from copy import deepcopy
from typing import Any

from research_kb.services.discovery_acquisition import (
    validate_acquired_candidate_source,
)
from research_kb.services.discovery_candidate import DiscoveryCandidateService
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.workspace import WorkspaceLayout


class AcquiredCandidateIntakeService:
    def __init__(self, layout: WorkspaceLayout):
        self.layout = layout

    def inspect(self, candidate_id: str) -> dict[str, Any]:
        candidate = DiscoveryCandidateService(self.layout).show(candidate_id)["candidate"]
        destination = validate_acquired_candidate_source(self.layout, candidate)
        intake = IntakeInspectService(self.layout).inspect(source=destination.final_path)
        validate_acquired_candidate_source(self.layout, candidate)

        metadata: dict[str, Any] = {
            "bibliography": {
                "title": candidate["title"],
                "authors": deepcopy(candidate["authors"]),
                "year": int(candidate["first_publication_date"][:4]),
                "doi": candidate["doi"],
            }
        }
        if candidate.get("fixture_origin") is not None:
            metadata["fixture_origin"] = candidate["fixture_origin"]

        return {
            "status": "success",
            "interface_version": "1.0",
            "candidate_id": candidate["candidate_id"],
            "source": intake["source"],
            "registration": intake["registration"],
            "domain_profile": intake["domain_profile"],
            "registry_metadata": metadata,
            "persistent_writes": 0,
        }


__all__ = ["AcquiredCandidateIntakeService"]
