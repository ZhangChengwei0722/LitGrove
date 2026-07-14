import json
from pathlib import Path

import pytest

from research_kb.contracts.validator import validate_bundle
from tests.fixture_factory import invalid_bundle


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = json.loads((ROOT / "tests" / "fixtures" / "invalid" / "expected.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize(("case", "expected_code"), sorted(EXPECTED.items()))
def test_invalid_bundle_hits_expected_gate(case: str, expected_code: str) -> None:
    actor = "cli"
    diagnostics = validate_bundle(invalid_bundle(case), actor=actor)
    assert expected_code in {diagnostic.code for diagnostic in diagnostics}


def test_schema_invalid_record_does_not_enter_cross_record_validation() -> None:
    malformed = {
        "records": [
            {"kind": "workspace", "record": {"contract_version": "1.0", "workspace": "not-a-mapping", "runtime": {}}}
        ]
    }
    diagnostics = validate_bundle(malformed)
    assert diagnostics
    assert {item.code for item in diagnostics} == {"RKBC-002"}


def test_internal_schema_is_not_a_public_record_kind() -> None:
    diagnostics = validate_bundle({"records": [{"kind": "definitions", "record": {"schema_version": "1.0"}}]})
    assert {item.code for item in diagnostics} == {"RKBC-003"}
