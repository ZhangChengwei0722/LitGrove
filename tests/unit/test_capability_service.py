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
            "layout_versions": ["m3a-2a"],
        },
        "parse_adapters": [
            {
                "adapter": "pdfplumber",
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
        "mutation_record_kinds": [
            "evidence",
            "paper-card",
            "question-mapping",
            "registry-paper",
            "review-memory",
            "review-queue",
        ],
        "read_commands": [
            "capability show",
            "guardian check",
            "intake inspect",
            "paper context",
            "paper status",
            "parse show",
            "question list",
            "question render",
            "question show",
            "review context",
        ],
        "features": {
            "real_pdf_parse": True,
            "stdin_json_handoff": True,
            "review_runtime": True,
            "step7_runtime": False,
        },
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
    assert report["features"]["real_pdf_parse"] is True
