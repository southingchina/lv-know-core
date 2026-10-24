import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "select_scope.py"


def load_module():
    spec = importlib.util.spec_from_file_location("select_scope_v2", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def manifest_row(**overrides):
    row = {
        "id": "card-001",
        "title": "Card 001",
        "blogger": "author-001",
        "primary_theme": "GEO",
        "relative_path": "cards/card-001.md",
        "note_type": "source_master",
        "source_type": "video",
        "source_unit_id": "unit-001",
        "source_unit_sha256": "1" * 64,
        "file_sha256": "2" * 64,
        "card_content_sha256": "3" * 64,
        "enrichment_sha256": "4" * 64,
        "source_context_hash": "5" * 64,
        "semantic_input_sha256": "a" * 64,
    }
    row.update(overrides)
    return row


def test_incremental_uses_semantic_hash_not_file_hash():
    module = load_module()
    row = manifest_row(
        id="card-001",
        file_sha256="b" * 64,
        card_content_sha256="c" * 64,
        semantic_input_sha256="a" * 64,
    )
    state = {"cards": {"card-001": {"semantic_input_sha256": "a" * 64}}}

    assert module.select_scope([row], state, mode="incremental") == []


def test_scope_preserves_source_master_fields_and_extensions():
    module = load_module()
    row = manifest_row(id="card-001", future_v2_field={"preserved": True})

    selected = module.select_scope([row], {}, mode="all")

    assert selected[0]["source_unit_id"] == row["source_unit_id"]
    assert selected[0]["card_content_sha256"] == row["card_content_sha256"]
    assert selected[0]["semantic_input_sha256"] == row["semantic_input_sha256"]
    assert selected[0]["future_v2_field"] == row["future_v2_field"]
    assert selected[0]["selection_reason"] == "all"


def test_incremental_selects_only_new_or_semantically_changed_rows_in_id_order():
    module = load_module()
    unchanged = manifest_row(id="card-002")
    changed = manifest_row(id="card-003", semantic_input_sha256="b" * 64)
    new = manifest_row(id="card-001")
    state = {
        "cards": {
            "card-002": {"semantic_input_sha256": "a" * 64},
            "card-003": {"semantic_input_sha256": "c" * 64},
        }
    }

    selected = module.select_scope([changed, unchanged, new], state, mode="incremental")

    assert [(row["id"], row["selection_reason"]) for row in selected] == [
        ("card-001", "new"),
        ("card-003", "changed"),
    ]


def test_v2_object_manifest_requires_schema_version_two():
    module = load_module()

    with pytest.raises(ValueError, match="schema_version 2"):
        module.select_scope({"cards": [manifest_row()]}, {}, mode="all")


def test_v2_manifest_requires_every_contract_field():
    module = load_module()
    row = manifest_row()
    del row["source_context_hash"]

    with pytest.raises(ValueError, match="source_context_hash"):
        module.select_scope([row], {}, mode="all")


def test_previous_state_rejects_invalid_semantic_hash():
    module = load_module()
    state = {"cards": {"card-001": {"semantic_input_sha256": "not-a-sha256"}}}

    with pytest.raises(ValueError, match="invalid semantic_input_sha256"):
        module.select_scope([manifest_row()], state, mode="incremental")


def test_previous_state_rejects_non_object_payload_even_when_empty():
    module = load_module()

    with pytest.raises(ValueError, match="Run state must be an object"):
        module.select_scope([manifest_row()], [], mode="incremental")
