"""Regressions from the original 100-card / 98-atom content audit."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_dify_single_document import atoms, load_module as load_sync
from test_stage_gate import load_module as load_gate
from test_layer2_cards import layer2_cards, make_unit, raw_transcript_from
import candidate_contract
import source_master_contract


def test_projection_keeps_literal_source_id_and_return_path():
    atom = atoms(1)[0]
    source_id = "AI素材-恐龙哥  AI 小王子-7667581937581641011-M001"
    source_path = "01-视频母卡/恐龙哥  AI 小王子/" + source_id + ".md"
    atom["source_card_ids"] = [source_id]
    atom["source_relative_paths"] = [source_path]
    text = load_sync().parent_text(atom)
    assert f"[SOURCE_CARD_ID:{source_id}]" in text
    assert f"[SOURCE_PATH:{source_path}]" in text


def test_multi_source_ids_preserve_the_same_identity_as_primary():
    atom = atoms(1)[0]
    source_id = "author  with  spaces-M001"
    atom["source_card_ids"] = [source_id, "second-M001"]
    text = load_sync().parent_text(atom)
    assert f"[SOURCE_CARD_ID:{source_id}]" in text
    assert source_id in json.loads(next(line[len("[ALL_SOURCE_CARD_IDS:"):-1] for line in text.splitlines() if line.startswith("[ALL_SOURCE_CARD_IDS:")))


def test_projection_rejects_reference_newlines_instead_of_rewriting_identity():
    atom = atoms(1)[0]
    atom["source_relative_paths"] = ["folder\nother/file.md"]
    sync = load_sync()
    with pytest.raises(sync.DifySyncError, match="source_reference_contains_newline"):
        sync.parent_text(atom)


def test_quote_and_comparison_completion_alone_do_not_authorize_publication(tmp_path):
    gate = load_gate()
    state = gate.create_state(tmp_path / "run", "manifest", "marker")
    gate.mark_compared(state, "comparison")
    assert gate.check_layer3_publish(state, "manifest", "marker") == {
        "ready": False, "reason": "content_review_not_passed"
    }


def review_files(tmp_path, gate, verdict="supported", reviewed_hash="a" * 64):
    preview = tmp_path / "atoms.jsonl"
    preview.write_text(json.dumps({"atom_id": "atom-1", "content_hash": "a" * 64}) + "\n", encoding="utf-8")
    receipt = tmp_path / "content-review.json"
    receipt.write_text(json.dumps({
        "policy": "source-fidelity-v1", "comparison_sha256": "comparison",
        "preview_path": str(preview), "preview_sha256": gate.sha256_file(preview),
        "reviews": [{"atom_id": "atom-1", "content_hash": reviewed_hash,
                     "verdict": verdict, "method": "semantic_sample", "reason": "Read claim, qualifiers, boundary and all relevant original-source context."}],
    }), encoding="utf-8")
    return preview, receipt


def test_review_is_bound_to_actual_preview_and_comparison(tmp_path):
    gate = load_gate()
    state = gate.create_state(tmp_path / "run", "manifest", "marker")
    gate.mark_compared(state, "comparison")
    preview, receipt = review_files(tmp_path, gate)
    gate.record_content_review(state, receipt)
    assert gate.check_layer3_publish(state, "manifest", "marker")["ready"]
    preview.write_text(preview.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert gate.check_layer3_publish(state, "manifest", "marker")["reason"] == "content_review_artifact_changed"


@pytest.mark.parametrize("verdict", ["revision_needed", "gray_needs_verification"])
def test_unresolved_atoms_cannot_be_in_active_preview(tmp_path, verdict):
    gate = load_gate()
    state = gate.create_state(tmp_path / "run", "manifest", "marker")
    gate.mark_compared(state, "comparison")
    _, receipt = review_files(tmp_path, gate, verdict)
    with pytest.raises(ValueError, match="active_atom_not_supported"):
        gate.record_content_review(state, receipt)


def test_reviewing_a_different_atom_version_does_not_count(tmp_path):
    gate = load_gate()
    state = gate.create_state(tmp_path / "run", "manifest", "marker")
    gate.mark_compared(state, "comparison")
    _, receipt = review_files(tmp_path, gate, reviewed_hash="b" * 64)
    with pytest.raises(ValueError, match="reviewed_atom_hash_mismatch"):
        gate.record_content_review(state, receipt)


def test_recomparison_requires_review_of_the_new_preview(tmp_path):
    gate = load_gate()
    state = gate.create_state(tmp_path / "run", "manifest", "marker")
    gate.mark_compared(state, "comparison")
    _, receipt = review_files(tmp_path, gate)
    gate.record_content_review(state, receipt)
    gate.mark_compared(state, "comparison-2")
    assert gate.check_layer3_publish(state, "manifest", "marker")["reason"] == "content_review_not_passed"


def test_article_source_is_not_labelled_as_spoken_transcript(tmp_path):
    raw = "# 文档章节\n正文原样保留。"
    unit = make_unit(tmp_path, raw, source_type="article_or_interview")
    text, _, _ = layer2_cards.render_card(unit, None)
    assert "<summary>完整原始正文</summary>" in text
    assert "完整原始口播稿" not in text
    assert raw_transcript_from(text) == raw


def test_article_original_body_is_consumed_by_source_only_candidate_reader(tmp_path):
    raw = "文档的原始正文。\n第二段完整保留。"
    unit = make_unit(tmp_path, raw, source_type="article_or_interview")
    text, _, _ = layer2_cards.render_card(unit, None)
    card = SimpleNamespace(frontmatter={"source_type": "article_or_interview"}, markdown=text)
    assert candidate_contract.model_evidence_segments(card) == (raw,)


def test_article_body_stays_out_of_derived_view_payload(tmp_path):
    raw = "这是唯一的完整正文，不得进入派生视图载荷。"
    unit = make_unit(tmp_path, raw, source_type="article_or_interview")
    text, _, _ = layer2_cards.render_card(unit, None)
    derived = source_master_contract._strip_original_source_blocks(text)
    assert raw not in derived
    assert "# 测试标题" in derived


def test_generation_input_prefers_original_over_a_derived_calibrated_summary():
    text = ('<details>\n<summary>原博主校对版口播稿</summary>\n'
            '<p>作者讲了一个很好的故事，这是改写分析。</p>\n</details>\n'
            '<details>\n<summary>完整原始口播稿</summary>\n'
            '<pre data-lv-know="raw-transcript">我先试一下。结果这个风格不行。</pre>\n</details>')
    card = SimpleNamespace(frontmatter={"source_type": "short_video"}, markdown=text)
    assert candidate_contract.model_evidence_segments(card) == ("我先试一下。结果这个风格不行。",)
