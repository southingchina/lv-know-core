from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import profile_config  # noqa: E402
import layer2_cards  # noqa: E402
import final_acceptance  # noqa: E402


def test_builtin_profiles_share_one_contract() -> None:
    for profile_id in ("geo", "ai", "ecommerce"):
        profile = profile_config.load_profile(profile_id)
        assert profile["profile_id"] == profile_id
        assert profile["routes"]
        assert profile["target_documents"]
        assert profile["templates"]["layer2"]
        assert profile["templates"]["layer3"]


def test_future_profile_is_added_by_config_only(tmp_path: Path) -> None:
    layer2_template = tmp_path / "layer2.md"
    layer3_template = tmp_path / "layer3.md"
    layer2_template.write_text("layer2", encoding="utf-8")
    layer3_template.write_text("layer3", encoding="utf-8")
    config = tmp_path / "custom.json"
    config.write_text(
        json.dumps(
            {
                "profile_id": "custom",
                "display_name": "自定义知识",
                "obsidian_vault_root": "D:/vault/custom",
                "routes": {"short_video": "02_cards", "default": "03_notes"},
                "dify_dataset": {"id": "dataset-custom", "name": "自定义知识库"},
                "target_documents": [
                    {
                        "document_id": "doc-default",
                        "document_name": "默认文档",
                        "match_rules": {"default": True},
                    },
                    {
                        "document_id": "doc-shop",
                        "document_name": "店铺文档",
                        "match_rules": {"subject_platform": "shop"},
                    },
                ],
                "templates": {"layer2": "layer2.md", "layer3": "layer3.md"},
                "taxonomy": {
                    "base_tags": ["自定义/来源", "自定义/作者/{blogger}"],
                    "theme_tag_format": "自定义/主题/{primary_theme}",
                    "allowed_additional_tags": ["自定义/案例"],
                    "filename_fallback": "自定义素材",
                },
                "domain_rules": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    profile = profile_config.load_profile("custom", config)
    assert profile_config.route_for_source(profile, "short_video") == "02_cards"
    assert profile_config.route_for_source(profile, "long_form") == "03_notes"
    assert profile_config.theme_tag(profile, "07") == "自定义/主题/07"
    assert profile_config.route_target_document(profile, {"subject_platform": "shop"})[
        "document_id"
    ] == "doc-shop"
    assert profile_config.route_target_document(profile, {"subject_platform": "other"})[
        "document_id"
    ] == "doc-default"

    raw = tmp_path / "raw.txt"
    raw.write_text("完整来源内容", encoding="utf-8")
    unit = {
        "unit_id": "custom-unit",
        "card_id": "custom-card",
        "blogger": "作者甲",
        "source_type": "short_video",
        "source_title": "自定义标题",
        "source_path": str(raw),
        "raw_text_path": str(raw),
        "source_sha256": "a" * 64,
        "source_locator": "00:00-00:10",
        "source_unit_sha256": "b" * 64,
        "idempotency_key": "c" * 64,
        "primary_theme": "07",
    }
    plan = layer2_cards.build_render_plan(
        {"schema_version": 2, "units": [unit]},
        {},
        profile="custom",
        profile_config_path=config,
    )
    assert plan[0]["relative_path"].parts[0] == "02_cards"
    assert "自定义/作者/作者甲" in plan[0]["card"]
    assert "自定义/主题/07" in plan[0]["card"]
    assert final_acceptance.normalize_sync_mode("segment_incremental") == "segment_incremental"
    assert final_acceptance.normalize_sync_mode("full_document_replace") == "full_document_replace"


def test_profile_rejects_ambiguous_default_documents(tmp_path: Path) -> None:
    template = tmp_path / "template.md"
    template.write_text("x", encoding="utf-8")
    payload = {
        "profile_id": "bad",
        "display_name": "bad",
        "obsidian_vault_root": "D:/vault/bad",
        "routes": {"default": "cards"},
        "dify_dataset": {"id": "ds", "name": "bad"},
        "target_documents": [
            {"document_name": "a", "match_rules": {"default": True}},
            {"document_name": "b", "match_rules": {"default": True}},
        ],
        "templates": {"layer2": "template.md", "layer3": "template.md"},
        "taxonomy": {
            "base_tags": [],
            "theme_tag_format": "主题/{primary_theme}",
            "allowed_additional_tags": [],
            "filename_fallback": "素材",
        },
        "domain_rules": [],
    }
    config = tmp_path / "bad.json"
    config.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one default"):
        profile_config.load_profile("bad", config)
