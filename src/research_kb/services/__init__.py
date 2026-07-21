"""Deterministic record services."""

from research_kb.services.acquired_candidate_intake import AcquiredCandidateIntakeService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.capability import CapabilityService
from research_kb.services.compatibility import CompatibilityAdapterRegistry, CompatibilityInspectionService
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.services.discovery_candidate import DiscoveryCandidateService, DiscoverySelectionResult
from research_kb.services.discovery_acquisition import (
    DiscoveryAcquisitionService,
    DiscoveryAcquisitionTransportRegistry,
)
from research_kb.services.discovery_resolution import DiscoveryResolutionService, DiscoveryResolverRegistry
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.services.parse import ParseService
from research_kb.services.parse_read import ParseReadService
from research_kb.services.paper_context import PaperContextService
from research_kb.services.review_context import ReviewContextService
from research_kb.services.review_memory import ReviewMemoryService
from research_kb.services.step7_candidate import Step7CandidateService
from research_kb.services.step7_context import Step7ContextService
from research_kb.services.step7_view import Step7ReadingViewService
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.question_view import QuestionReadingViewService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService

__all__ = [
    "AcquiredCandidateIntakeService",
    "CapabilityService",
    "CompatibilityAdapterRegistry",
    "CompatibilityInspectionService",
    "DiscoveryConnectorRegistry",
    "DiscoveryAcquisitionService",
    "DiscoveryAcquisitionTransportRegistry",
    "DiscoveryCandidateService",
    "DiscoverySelectionResult",
    "DiscoveryResolutionService",
    "DiscoveryResolverRegistry",
    "DiscoveryService",
    "IntakeInspectService",
    "ParseService",
    "ParseReadService",
    "PaperContextService",
    "ReviewContextService",
    "ReviewMemoryService",
    "Step7CandidateService",
    "Step7ContextService",
    "Step7ReadingViewService",
    "QuestionMappingService",
    "QuestionReadingViewService",
    "RecordService",
    "RegistryService",
    "WorkspaceBootstrapService",
]
