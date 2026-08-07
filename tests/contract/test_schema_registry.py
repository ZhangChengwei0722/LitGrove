from collections import Counter

from jsonschema import Draft202012Validator

from research_kb.contracts import registry as registry_module
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


def test_default_schema_text_is_read_once_across_registry_instances(monkeypatch) -> None:
    original = registry_module._read_default_schema_text_uncached
    reads: list[str] = []

    def counted(filename: str) -> str:
        reads.append(filename)
        return original(filename)

    monkeypatch.setattr(registry_module, "_read_default_schema_text_uncached", counted)
    registry_module._read_default_schema_text.cache_clear()
    try:
        SchemaRegistry().schemas()
        SchemaRegistry().schemas()
    finally:
        registry_module._read_default_schema_text.cache_clear()

    assert Counter(reads) == Counter(SCHEMA_FILES.values())


def test_custom_schema_root_bypasses_the_default_cache(tmp_path) -> None:
    schema_root = tmp_path / "schemas" / "1.0"
    schema_root.mkdir(parents=True)
    schema = schema_root / "workspace.schema.json"
    schema.write_text("first", encoding="utf-8")

    first = SchemaRegistry(tmp_path / "schemas")._read_text("workspace.schema.json")
    schema.write_text("second", encoding="utf-8")
    second = SchemaRegistry(tmp_path / "schemas")._read_text("workspace.schema.json")

    assert (first, second) == ("first", "second")
