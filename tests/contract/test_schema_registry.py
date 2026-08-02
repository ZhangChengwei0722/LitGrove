from jsonschema import Draft202012Validator

from research_kb.contracts.registry import SCHEMA_FILES, SchemaRegistry


def test_all_declared_schemas_are_valid_draft_2020_12() -> None:
    registry = SchemaRegistry()
    assert set(registry.schemas()) == set(SCHEMA_FILES)
    for schema in registry.schemas().values():
        Draft202012Validator.check_schema(schema)


def test_public_kinds_exclude_internal_definition_schemas() -> None:
    kinds = SchemaRegistry().kinds
    assert "definitions" not in kinds
    assert "step7-common" not in kinds
    assert "paper-card" in kinds
    assert "review-memory" in kinds
    assert "step7-insight" in kinds
    assert "source-adequacy-profile" in kinds
    assert "knowledge-query-report" in kinds
