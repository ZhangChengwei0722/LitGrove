from importlib.metadata import version

from research_kb.services.capability import CapabilityService


def test_capability_report_is_exact_sorted_and_workspace_independent() -> None:
    report = CapabilityService(pdfplumber_probe=lambda: version("pdfplumber")).show()

    assert report == {
        "status": "success",
        "interface_version": "1.0",
        "core": {
            "version": "0.1.0",
            "contract_versions": ["1.0"],
            "layout_versions": ["p4b-1"],
        },
        "parse_adapters": [
            {
                "adapter": "pdfplumber",
                "availability": "available",
                "version": version("pdfplumber"),
                "diagnostic_code": None,
            },
            {
                "adapter": "pdfplumber-text-flow",
                "availability": "available",
                "version": version("pdfplumber"),
                "diagnostic_code": None,
            },
            {
                "adapter": "synthetic-text",
                "availability": "available",
                "version": "1.0",
                "diagnostic_code": None,
            },
        ],
        "discovery_connectors": [
            {
                "connector": "europe-pmc",
                "availability": "available",
                "network_required": True,
            },
        ],
        "mutation_record_kinds": [
            "evidence",
            "paper-card",
            "question-mapping",
            "registry-paper",
            "review-memory",
            "review-queue",
            "step7-cross-view",
            "step7-insight",
            "step7-review-angle",
            "step7-synthesis",
        ],
        "read_commands": [
            "adequacy gate",
            "adequacy show",
            "capability show",
            "discovery list",
            "discovery resolve",
            "discovery search",
            "discovery show",
            "guardian check",
            "identity list",
            "intake inspect",
            "intake inspect-acquired",
            "job list",
            "job show",
            "manuscript inspect",
            "paper context",
            "paper status",
            "parse show",
            "question list",
            "question render",
            "question show",
            "review context",
            "source list",
            "source scan",
            "step7 context",
            "step7 render",
        ],
        "write_commands": [
            "adequacy assess",
            "guardian disposition",
            "identity correct",
            "job cancel",
            "job create",
            "job recover",
            "job transition",
            "source associate",
            "source copy",
            "source observe",
            "source reference",
            "source relink",
            "source select",
            "trunk advance",
        ],
        "operational_record_kinds": [
            "agent-task-state",
            "guardian-finding-disposition",
            "guardian-report",
            "pipeline-job-state",
            "process-event",
            "registry-identity-correction",
            "source-adequacy-profile",
            "source-asset-state",
            "transaction-journal",
        ],
        "features": {
            "approved_discovery_candidate_handoff": True,
            "explicit_oa_acquisition": True,
            "legal_oa_resolution": True,
            "manuscript_projection": True,
            "real_pdf_parse": True,
            "stdin_json_handoff": True,
            "review_runtime": True,
            "step7_runtime": True,
            "on_demand_discovery": True,
            "pipeline_jobs": True,
            "registry_identity_correction": True,
            "source_asset_runtime": True,
            "source_adequacy": True,
            "deterministic_trunk": True,
            "deterministic_intake_application": True,
            "agent_task_staging": True,
            "embedded_agent_runtime": False,
        },
        "agent_task_registry_version": "p4b-v1",
    }


def test_capability_report_treats_missing_pdf_extra_as_availability_fact() -> None:
    report = CapabilityService(pdfplumber_probe=lambda: None).show()

    assert report["status"] == "success"
    assert report["parse_adapters"][0] == {
        "adapter": "pdfplumber",
        "availability": "dependency_missing",
        "version": None,
        "diagnostic_code": "RKBC-028",
    }
    assert report["parse_adapters"][1] == {
        "adapter": "pdfplumber-text-flow",
        "availability": "dependency_missing",
        "version": None,
        "diagnostic_code": "RKBC-028",
    }
    assert report["features"]["real_pdf_parse"] is True
