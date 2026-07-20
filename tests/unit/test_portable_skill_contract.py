from pathlib import Path
import re

import yaml

from research_kb.services.capability import CapabilityService


ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = ROOT / "skills" / "research-kb"
EXPECTED_FILES = {
    "SKILL.md",
    "agents/openai.yaml",
    "references/authority-and-failure-boundaries.md",
    "references/cli-contract.md",
    "references/local-intake-workflow.md",
    "references/task" "-report-contract.md",
}


def _skill_parts() -> tuple[dict[str, str], str]:
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    _, frontmatter, body = content.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert isinstance(metadata, dict)
    return metadata, body


def _package_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(SKILL_ROOT.rglob("*"))
        if path.is_file()
    )


def test_portable_skill_has_exact_minimal_file_set() -> None:
    actual = {
        path.relative_to(SKILL_ROOT).as_posix()
        for path in SKILL_ROOT.rglob("*")
        if path.is_file()
    }

    assert actual == EXPECTED_FILES
    assert not (SKILL_ROOT / "scripts").exists()
    assert not (SKILL_ROOT / "assets").exists()


def test_skill_frontmatter_and_progressive_disclosure_contract() -> None:
    metadata, body = _skill_parts()

    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "research-kb"
    description = metadata["description"].lower()
    for trigger in (
        "workspace",
        "local",
        "primary",
        "pdf",
        "resume",
        "status",
        "evidence",
        "question",
        "guardian",
    ):
        assert trigger in description
    assert len((SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8").splitlines()) < 500
    for reference in sorted(path for path in EXPECTED_FILES if path.startswith("references/")):
        target = reference.replace("task" + "-", "task%2D")
        assert f"]({target})" in body


def test_openai_metadata_is_exact_and_mentions_skill() -> None:
    metadata = yaml.safe_load((SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"))

    assert metadata == {
        "interface": {
            "display_name": "Research KB",
            "short_description": "Process local papers with traceable evidence",
            "default_prompt": (
                "Use $research-kb to ingest a local primary-research PDF and process it through Guardian."
            ),
        }
    }


def test_skill_is_generic_private_safe_and_has_no_hidden_fallback() -> None:
    text = _package_text()

    for forbidden in (
        "E" + ":\\",
        "C:" + "\\Users",
        "Q" + "001",
        "T" + "PD",
        "doi.org/",
        "research-kb ingest",
        "research-kb review",
        "research-kb step7",
        "research-kb discover",
        "research-kb workspace create",
    ):
        assert forbidden not in text
    assert re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.IGNORECASE) is None
    assert "Do not parse workspace or domain-profile configuration" in text
    assert "Do not read canonical JSON or JSONL files directly" in text
    assert "Review processing is not implemented" in text
    assert "Step 7 is not implemented" in text


def test_skill_required_read_commands_match_public_capability() -> None:
    capability = CapabilityService(pdfplumber_probe=lambda: "0.11.10").show()
    required_reads = {
        "capability show",
        "guardian check",
        "intake inspect",
        "paper context",
        "paper status",
        "parse show",
        "question list",
        "question show",
    }

    assert required_reads <= set(capability["read_commands"])
    assert capability["features"]["real_pdf_parse"] is True
    assert capability["features"]["review_runtime"] is False
    assert capability["features"]["step7_runtime"] is False


def test_cli_reference_contains_minimal_stdin_promotion_envelopes() -> None:
    text = (SKILL_ROOT / "references" / "cli-contract.md").read_text(encoding="utf-8")

    for record_kind in ("evidence", "review-queue", "paper-card", "question-mapping"):
        assert f'"record_kind": "{record_kind}"' in text
    for required_field in (
        '"target_record_id": null',
        '"context":',
        '"payload":',
        '"what_it_does_not_support":',
        '"selected_card_unit_ids":',
        '"question_origin": "user_supplied"',
    ):
        assert required_field in text
    assert "Do not submit CLI-owned IDs" in text
    assert "Omit `fixture_origin` in real tasks" in text
    for statement_type in (
        "background",
        "method_description",
        "reported_result",
        "author_conclusion",
        "limitation",
        "future_direction",
        "interpretation",
    ):
        assert f"| `{statement_type}` |" in text
    assert "There is no Card `other` type" in text


def test_task_report_defines_new_and_no_change_completion_outcomes() -> None:
    authority = (SKILL_ROOT / "references" / "authority-and-failure-boundaries.md").read_text(encoding="utf-8")
    report = (SKILL_ROOT / "references" / ("task" + "-report-contract.md")).read_text(encoding="utf-8")

    assert "| `completed` |" in authority
    assert "| `completed_no_change` |" in authority
    assert "Use `completed` when this run newly reaches Guardian" in report
