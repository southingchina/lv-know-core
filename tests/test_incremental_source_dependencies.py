"""Exercise cache selection with real source-only payloads and real manifests."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from candidate_contract import model_evidence_segments
from select_scope import select_scope
from source_master_contract import (
    SemanticVersions, build_manifest_v2, read_source_master, semantic_input_sha256,
)


VERSIONS = SemanticVersions("domain-1", "questions-1", "schema-1")


def source_markdown(card_id="card-1", *, raw="作者说先收集资料，再整理结论。", derived="原来的钩子分析。"):
    return (
        "---\n"
        f"id: {card_id}\nlayer: 2\nnote_type: source_master\neligible_for_l3: true\n"
        f"source_unit_id: unit-{card_id}\nsource_unit_sha256: {'a' * 64}\n"
        "source_type: short_video\nauthor_id: author-1\nprimary_theme: AI\n"
        "source_path: original.md\nsource_locator: whole-file\n"
        "---\n\n# 卡片展示标题\n\n## 开场钩子\n\n"
        f"> 整理资料能解决什么问题？\n\n{derived}\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n\n"
        f'<pre data-lv-know="raw-transcript">{raw}</pre>\n\n</details>\n'
    )


@pytest.mark.parametrize("derived", [
    "新的钩子机制分析。", "### 上下文术语恢复\n显示名纠正，原稿不变。",
    "### 旧提炼记录\n旧记录归档。", "### 类型说明\n页面展示说明更新。",
])
def test_derived_only_changes_do_not_invalidate_source_only_cache(tmp_path, derived):
    path = tmp_path / "card.md"
    path.write_text(source_markdown(), encoding="utf-8")
    before = read_source_master(path, tmp_path)
    after = read_source_master(path, tmp_path, planned_markdown=source_markdown(derived=derived))
    assert before.card_content_sha256 != after.card_content_sha256
    assert before.enrichment_sha256 != after.enrichment_sha256
    assert model_evidence_segments(before) == model_evidence_segments(after)
    assert semantic_input_sha256(before, VERSIONS) == semantic_input_sha256(after, VERSIONS)


def test_changed_raw_invalidates_cache_even_when_declared_unit_hash_is_stale(tmp_path):
    path = tmp_path / "card.md"
    path.write_text(source_markdown(), encoding="utf-8")
    before = read_source_master(path, tmp_path)
    after = read_source_master(path, tmp_path, planned_markdown=source_markdown(raw="作者说先定义问题，再收集资料。"))
    assert before.source_unit_sha256 == after.source_unit_sha256
    assert before.enrichment_sha256 == after.enrichment_sha256
    assert model_evidence_segments(before) != model_evidence_segments(after)
    assert semantic_input_sha256(before, VERSIONS) != semantic_input_sha256(after, VERSIONS)


@pytest.mark.parametrize("old,new", [
    ("author_id: author-1", "author_id: author-2"),
    ("primary_theme: AI", "primary_theme: 电商"),
    ("source_type: short_video", "source_type: article_or_interview"),
    ("source_unit_id: unit-card-1", "source_unit_id: another-source-unit"),
])
def test_authoritative_metadata_changes_invalidate_cache(tmp_path, old, new):
    path = tmp_path / "card.md"
    path.write_text(source_markdown(), encoding="utf-8")
    before = read_source_master(path, tmp_path)
    after = read_source_master(path, tmp_path, planned_markdown=source_markdown().replace(old,new))
    assert model_evidence_segments(before) == model_evidence_segments(after)
    assert semantic_input_sha256(before, VERSIONS) != semantic_input_sha256(after, VERSIONS)


@pytest.mark.parametrize("versions", [
    SemanticVersions("domain-2", "questions-1", "schema-1"),
    SemanticVersions("domain-1", "questions-2", "schema-1"),
    SemanticVersions("domain-1", "questions-1", "schema-2"),
])
def test_changed_semantic_rules_still_invalidate_cache(tmp_path, versions):
    path = tmp_path / "card.md"
    path.write_text(source_markdown(), encoding="utf-8")
    card = read_source_master(path, tmp_path)
    assert semantic_input_sha256(card, VERSIONS) != semantic_input_sha256(card, versions)


def test_no_source_projection_falls_back_conservatively(tmp_path):
    path = tmp_path / "card.md"
    first_text = source_markdown().split("<details>")[0]
    path.write_text(first_text, encoding="utf-8")
    before = read_source_master(path, tmp_path)
    after = read_source_master(path, tmp_path, planned_markdown=first_text.replace("原来的钩子分析。", "发生变更。"))
    assert not model_evidence_segments(before) and not model_evidence_segments(after)
    assert semantic_input_sha256(before, VERSIONS) != semantic_input_sha256(after, VERSIONS)


def test_real_incremental_selection_reuses_hook_edit_and_selects_changed_source(tmp_path):
    legacy = []
    for card_id in ["hook-only", "source-changed"]:
        path = tmp_path / f"{card_id}.md"
        path.write_text(source_markdown(card_id), encoding="utf-8")
        legacy.append({"id":card_id,"relative_path":path.name})
    baseline = build_manifest_v2(tmp_path, legacy, VERSIONS)
    previous = {"cards":{row["id"]:{"semantic_input_sha256":row["semantic_input_sha256"]} for row in baseline["cards"]}}
    changed = build_manifest_v2(tmp_path, legacy, VERSIONS, planned_markdown_by_id={
        "hook-only":source_markdown("hook-only",derived="仅改了钩子说明。"),
        "source-changed":source_markdown("source-changed",raw="新原稿明确要求先定义问题。"),
    })
    selected = select_scope(changed, previous, mode="incremental")
    assert [(row["id"],row["selection_reason"]) for row in selected] == [("source-changed","changed")]
