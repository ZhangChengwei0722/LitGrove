"""Deterministic record services."""

from research_kb.services.acquired_candidate_intake import AcquiredCandidateIntakeService
from research_kb.services.application_validation import ContractValidationService, JsonlValidationService
from research_kb.services.agent_task_application import AgentTaskApplicationService
from research_kb.services.bootstrap import WorkspaceBootstrapService
from research_kb.services.catalog import CatalogCapabilityService, CatalogProjectionService, CatalogQueryService
from research_kb.services.capability import CapabilityService
from research_kb.services.compatibility import CompatibilityAdapterRegistry, CompatibilityInspectionService
from research_kb.services.discovery import DiscoveryConnectorRegistry, DiscoveryService
from research_kb.services.discovery_application import DiscoveryApplicationService
from research_kb.services.discovery_candidate import DiscoveryCandidateService, DiscoverySelectionResult
from research_kb.services.discovery_acquisition import (
    DiscoveryAcquisitionService,
    DiscoveryAcquisitionTransportRegistry,
)
from research_kb.services.discovery_resolution import DiscoveryResolutionService, DiscoveryResolverRegistry
from research_kb.services.deterministic_trunk import DeterministicTrunkResult, DeterministicTrunkService
from research_kb.services.deterministic_intake_application import DeterministicIntakeApplicationService
from research_kb.services.exchange_application import ExchangeApplicationService
from research_kb.exchange_import import ExchangeArchiveReader, ExchangeImportService, SafeReaderProfile
from research_kb.services.intake_inspect import IntakeInspectService
from research_kb.services.guardian_disposition import GuardianFindingDispositionService
from research_kb.services.local_source_intake import LocalSourceIntakeService
from research_kb.services.manuscript_projection import ManuscriptProjectionService
from research_kb.services.obsidian_generated_views import ObsidianGeneratedViewsService
from research_kb.services.obsidian_generated_views_application import (
    ObsidianGeneratedViewsApplicationService,
)
from research_kb.services.parse import ParseService
from research_kb.services.parse_application import ParseAdapterRegistry, ParseApplicationService
from research_kb.services.parse_read import ParseReadService
from research_kb.services.paper_context import PaperContextService
from research_kb.services.pipeline_job import PipelineJobService
from research_kb.services.review_context import ReviewContextService
from research_kb.services.review_memory import ReviewMemoryService
from research_kb.services.privacy_scan import PrivacyScanService
from research_kb.services.question_read import QuestionQueryService, WorkspaceQuestionReadingViewService
from research_kb.services.reading_application import (
    EvidenceSourceHandle,
    OpenedEvidenceSource,
    PreparedEvidenceSource,
    ReadingApplicationService,
)
from research_kb.services.recovery import TransactionRecoveryService
from research_kb.services.step7_candidate import Step7CandidateService
from research_kb.services.step7_context import Step7ContextService
from research_kb.services.step7_render import WorkspaceStep7ReadingViewService
from research_kb.services.step7_view import Step7ReadingViewService
from research_kb.services.workspace_session import WorkspaceSession, WorkspaceSessionService
from research_kb.services.workspace_storage import WorkspaceStorageInspectionService, WorkspaceStorageRoots
from research_kb.services.question_mapping import QuestionMappingService
from research_kb.services.question_screening import QuestionScreeningService
from research_kb.services.question_screening_application import QuestionScreeningApplicationService
from research_kb.services.research_organization import ResearchOrganizationService
from research_kb.services.research_organization_application import ResearchOrganizationApplicationService
from research_kb.services.research_synthesis_application import ResearchSynthesisApplicationService
from research_kb.services.tag_application import TagApplicationService
from research_kb.services.tags import TagService
from research_kb.services.question_view import QuestionReadingViewService
from research_kb.services.records import RecordService
from research_kb.services.registry import RegistryService
from research_kb.services.registry_identity import RegistryIdentityCorrectionService
from research_kb.services.source_asset import SourceAssetService
from research_kb.services.source_adequacy import SourceAdequacyMutationResult, SourceAdequacyService

__all__ = [
    "AcquiredCandidateIntakeService",
    "AgentTaskApplicationService",
    "CapabilityService",
    "CatalogCapabilityService",
    "CatalogProjectionService",
    "CatalogQueryService",
    "CompatibilityAdapterRegistry",
    "CompatibilityInspectionService",
    "ContractValidationService",
    "DiscoveryConnectorRegistry",
    "DiscoveryApplicationService",
    "DiscoveryAcquisitionService",
    "DiscoveryAcquisitionTransportRegistry",
    "DiscoveryCandidateService",
    "DiscoverySelectionResult",
    "DiscoveryResolutionService",
    "DiscoveryResolverRegistry",
    "DiscoveryService",
    "DeterministicTrunkResult",
    "DeterministicTrunkService",
    "DeterministicIntakeApplicationService",
    "ExchangeApplicationService",
    "ExchangeArchiveReader",
    "ExchangeImportService",
    "SafeReaderProfile",
    "IntakeInspectService",
    "GuardianFindingDispositionService",
    "JsonlValidationService",
    "LocalSourceIntakeService",
    "ManuscriptProjectionService",
    "ObsidianGeneratedViewsApplicationService",
    "ObsidianGeneratedViewsService",
    "ParseService",
    "ParseAdapterRegistry",
    "ParseApplicationService",
    "ParseReadService",
    "PaperContextService",
    "PipelineJobService",
    "PrivacyScanService",
    "QuestionQueryService",
    "EvidenceSourceHandle",
    "OpenedEvidenceSource",
    "PreparedEvidenceSource",
    "ReadingApplicationService",
    "ReviewContextService",
    "ReviewMemoryService",
    "Step7CandidateService",
    "Step7ContextService",
    "Step7ReadingViewService",
    "TransactionRecoveryService",
    "QuestionMappingService",
    "QuestionScreeningApplicationService",
    "QuestionScreeningService",
    "ResearchOrganizationApplicationService",
    "ResearchOrganizationService",
    "ResearchSynthesisApplicationService",
    "TagApplicationService",
    "TagService",
    "QuestionReadingViewService",
    "RecordService",
    "RegistryService",
    "RegistryIdentityCorrectionService",
    "SourceAssetService",
    "SourceAdequacyMutationResult",
    "SourceAdequacyService",
    "WorkspaceBootstrapService",
    "WorkspaceSession",
    "WorkspaceSessionService",
    "WorkspaceStorageInspectionService",
    "WorkspaceStorageRoots",
    "WorkspaceQuestionReadingViewService",
    "WorkspaceStep7ReadingViewService",
]
