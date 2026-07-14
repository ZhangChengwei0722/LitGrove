from pathlib import Path

import pytest

from research_kb.config.loader import load_config
from research_kb.contracts.validator import validate_bundle, validate_record
from tests.fixture_factory import make_bundle


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("domain", ["alpha", "beta"])
def test_two_domains_share_one_public_contract(domain: str) -> None:
    bundle = make_bundle(domain)
    assert validate_bundle(bundle, actor="cli") == []


@pytest.mark.parametrize("domain", ["alpha", "beta"])
def test_every_generated_record_validates_individually(domain: str) -> None:
    for entry in make_bundle(domain)["records"]:
        assert validate_record(entry["kind"], entry["record"], actor="cli") == []


def test_public_templates_validate() -> None:
    load_config(ROOT / "templates" / "workspace.example.yaml", "workspace")
    load_config(ROOT / "templates" / "domain-profile.example.yaml", "domain-profile")


def test_one_core_maps_different_units_to_multiple_questions() -> None:
    mappings = [entry["record"] for entry in make_bundle("alpha")["records"] if entry["kind"] == "question-mapping"]
    first_paper = mappings[0]["paper_links"][0]["paper_id"]
    first_units = set(mappings[0]["paper_links"][0]["selected_card_unit_ids"])
    second_units = set(mappings[1]["paper_links"][0]["selected_card_unit_ids"])
    assert mappings[1]["paper_links"][0]["paper_id"] == first_paper
    assert first_units.isdisjoint(second_units)
