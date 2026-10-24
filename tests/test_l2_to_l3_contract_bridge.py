from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import candidate_contract  # noqa: E402
import layer2_cards  # noqa: E402
import layer2_source  # noqa: E402
import select_scope  # noqa: E402
import source_master_contract  # noqa: E402
import validate_layer2_stage  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_rendered_layer2_card_flows_into_layer3_contract(tmp_path: Path) -> None:
    source = tmp_path / "lesson.txt"
    source.write_text("当预算不足时，先缩小投放范围。", encoding="utf-8")
    run = tmp_path / "run"
    spec = run / "source-spec.json"
    write_json(
        spec,
        {
            "schema_version": 2,
            "units": [
                {
                    "unit_id": "lesson-001",
                    "card_id": "EC-LESSON-001",
                    "blogger": "测试讲师",
                    "source_title": "预算不足时如何调整投放",
                    "path": str(source),
                    "source_type": "long_form",
                    "primary_theme": "02",
                    "locator": "course:测试课/lesson:1/time:00:00-00:20",
                    "group_id": None,
                    "order": None,
                }
            ],
        },
    )
    inventory_path = layer2_source.build_inventory(spec, run)
    layer2_source.build_context_packets(
        inventory_path, run / "context-map.json", run / "context-packets.jsonl"
    )
    staged_path = layer2_cards.render_stage(
        inventory_path, None, run, profile="ecommerce"
    )

    validation = validate_layer2_stage.validate_layer2_stage(run)
    assert validation["status"] == "pass"

    staged = layer2_source.read_json(staged_path)
    row = staged["cards"][0]
    card_path = Path(row["output_path"])
    vault_root = run / "layer2-cards"
    card = source_master_contract.read_source_master(card_path, vault_root)
    inventory_unit = layer2_source.read_json(inventory_path)["units"][0]
    assert card.frontmatter["source_unit_id"] == inventory_unit["unit_id"]
    assert card.frontmatter["idempotency_key"] == inventory_unit["idempotency_key"]
    assert row["source_unit_id"] == card.frontmatter["source_unit_id"]
    assert row["idempotency_key"] == card.frontmatter["idempotency_key"]

    versions = source_master_contract.SemanticVersions("domain-v1", "questions-v1", "extract-v1")
    manifest = source_master_contract.build_manifest_v2(
        vault_root,
        [{"id": row["id"], "title": "预算不足时如何调整投放", "relative_path": row["relative_path"]}],
        versions,
    )
    selected = select_scope.select_scope(manifest, None, mode="all")
    assert [item["id"] for item in selected] == [row["id"]]

    evidence = "当预算不足时，先缩小投放范围。"
    locator = candidate_contract.locate_source_claim(card, evidence)
    assert locator is not None
    candidate_contract.validate_candidate(
        {
            "candidate_id": "candidate-001",
            "source_card_id": card.id,
            "source_unit_id": card.frontmatter["source_unit_id"],
            "source_card_sha256": card.card_content_sha256,
            "semantic_input_sha256": manifest["cards"][0]["semantic_input_sha256"],
            "question_key": "预算不足时如何调整投放",
            "claim": evidence,
            "source_claim": evidence,
            "source_evidence_locator": locator.locator,
            "source_blogger": "测试讲师",
            "source_type": "long_form",
            "primary_theme": "02",
            "related_themes": [],
            "evidence_status": "source_viewpoint",
            "boundary": "仅代表该课程来源的做法。",
            "disposition": "进入三层",
            "disposition_reason": "",
        },
        card,
        manifest["cards"][0]["semantic_input_sha256"],
    )


def test_profile_routes_are_explicit_and_ecommerce_long_form_uses_course_notes(tmp_path: Path) -> None:
    unit = {
        "unit_id": "u1",
        "card_id": "c1",
        "blogger": "作者",
        "source_title": "标题",
        "source_path": str(tmp_path / "source.txt"),
        "source_sha256": "a" * 64,
        "source_locator": "whole-file",
        "source_unit_sha256": "b" * 64,
        "idempotency_key": "c" * 64,
        "source_type": "long_form",
        "primary_theme": "02",
        "raw_text_path": str(tmp_path / "raw.txt"),
    }
    Path(unit["raw_text_path"]).write_text("正文", encoding="utf-8")
    inventory = {"schema_version": 2, "units": [unit]}

    assert layer2_cards.build_render_plan(inventory, {}, profile="geo")[0]["relative_path"].parts[0] == "01-来源母卡"
    assert layer2_cards.build_render_plan(inventory, {}, profile="ai")[0]["relative_path"].parts[0] == "01-视频母卡"
    assert layer2_cards.build_render_plan(inventory, {}, profile="ecommerce")[0]["relative_path"].parts[0] == "03_课程笔记"

