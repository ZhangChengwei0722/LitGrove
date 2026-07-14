from research_kb.contracts.validator import validate_record
from tests.fixture_factory import make_bundle


def test_agent_cannot_assign_automation_result() -> None:
    evidence = next(entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "evidence")
    diagnostics = validate_record("evidence", evidence, actor="agent")
    assert "RKBC-006" in {item.code for item in diagnostics}


def test_cli_may_validate_automation_result() -> None:
    evidence = next(entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "evidence")
    assert validate_record("evidence", evidence, actor="cli") == []


def test_agent_cannot_assign_final_screening_state() -> None:
    paper = next(entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "registry-paper")
    paper["screening_status"] = "included"
    diagnostics = validate_record("registry-paper", paper, actor="agent")
    assert "RKBC-006" in {item.code for item in diagnostics}


def test_user_may_assign_human_review_and_final_screening() -> None:
    paper = next(entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "registry-paper")
    paper["screening_status"] = "excluded"
    paper["review_status"] = "human_checked"
    assert validate_record("registry-paper", paper, actor="user") == []


def test_stored_context_accepts_existing_human_review_state() -> None:
    evidence = next(entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "evidence")
    evidence["review_status"] = "verified"

    assert validate_record("evidence", evidence, actor="stored") == []
