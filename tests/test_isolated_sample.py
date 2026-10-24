import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"lvknow_{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def make_preview(preview: Path, bloggers: tuple[str, str]) -> None:
    preview.mkdir()
    cards = [
        {"id": "card-a", "sha256": "a" * 64, "relative_path": "cards/card-a.md"},
        {"id": "card-b", "sha256": "b" * 64, "relative_path": "cards/card-b.md"},
    ]
    candidates = [
        {
            "candidate_id": "candidate-a",
            "answerable_question": "企业为什么不能只看排名截图？",
            "question_keys": ["geo-acceptance-evidence"],
            "claim": "企业验收 GEO 时不能只看单次排名截图。",
            "source_card_id": "card-a",
            "source_blogger": bloggers[0],
            "source_card_sha256": "a" * 64,
            "candidate_hash": "candidate-hash-a",
            "disposition": "进入三层",
        },
        {
            "candidate_id": "candidate-b",
            "answerable_question": "企业为什么不能只看排名截图？",
            "question_keys": ["geo-acceptance-evidence"],
            "claim": "单张排名图不足以证明 GEO 交付效果。",
            "source_card_id": "card-b",
            "source_blogger": bloggers[1],
            "source_card_sha256": "b" * 64,
            "candidate_hash": "candidate-hash-b",
            "disposition": "进入三层",
        },
    ]
    atom = {
        "atom_id": "atom-acceptance-evidence",
        "answerable_question": "企业为什么不能只看排名截图？",
        "question_keys": ["geo-acceptance-evidence"],
        "claim": "单次排名截图不足以单独证明 GEO 的持续交付效果。",
        "type": "判断",
        "knowledge_status": "多人共识",
        "primary_theme": "04",
        "related_themes": [],
        "boundary": "用于企业验收，不否定截图作为辅助证据。",
        "source_card_ids": ["card-a", "card-b"],
        "source_candidate_ids": ["candidate-a", "candidate-b"],
        "related_atom_ids": [],
        "version": 1,
        "content_hash": "atom-content-hash",
    }
    write_json(preview / "scope.json", {"schema_version": 1, "count": 2, "cards": cards})
    write_jsonl(preview / "candidates.jsonl", candidates)
    write_jsonl(preview / "atoms.jsonl", [atom])
    write_jsonl(
        preview / "atom-index.jsonl",
        [
            {
                "atom_id": atom["atom_id"],
                "question_keys": atom["question_keys"],
                "primary_theme": atom["primary_theme"],
                "related_themes": [],
                "knowledge_status": atom["knowledge_status"],
                "content_hash": atom["content_hash"],
                "source_count": 2,
            }
        ],
    )
    write_json(
        preview / "question-registry.json",
        {"schema_version": 1, "questions": [{"question_key": "geo-acceptance-evidence"}]},
    )
    write_json(
        preview / "source-map.json",
        {
            "schema_version": 1,
            "card_to_atoms": {
                "card-a": ["atom-acceptance-evidence"],
                "card-b": ["atom-acceptance-evidence"],
            },
            "atom_to_cards": {"atom-acceptance-evidence": ["card-a", "card-b"]},
        },
    )


def test_overlap_preserves_two_layer2_cards_and_merges_one_consensus_atom(tmp_path: Path) -> None:
    gate = load("stage_gate")
    validate = load("validate_preview")
    live_manifest = tmp_path / "formal-layer2-manifest.json"
    live_manifest.write_text('{"cards": []}\n', encoding="utf-8")
    live_before = live_manifest.read_bytes()
    state = gate.create_state(tmp_path / "run", gate.sha256_file(live_manifest), "marker-1")
    staged_manifest = tmp_path / "staged-layer2-manifest.json"
    write_json(
        staged_manifest,
        {
            "cards": [
                {"id": "card-a", "relative_path": "cards/card-a.md", "source_sha256": "a" * 64},
                {"id": "card-b", "relative_path": "cards/card-b.md", "source_sha256": "b" * 64},
            ]
        },
    )
    staged = gate.record_staged_manifest(state, staged_manifest)
    preview = tmp_path / "preview"
    make_preview(preview, ("博主甲", "博主乙"))
    write_json(preview / "build-metrics.json", validate.derive_metrics(preview))

    result = validate.validate_preview(preview)

    assert staged["layer2_card_count"] == 2
    assert result["counts"] == {
        "candidates": 2,
        "atoms": 1,
        "mapped_cards": 2,
        "pending_candidates": 0,
        "layer2_only_candidates": 0,
    }
    assert live_manifest.read_bytes() == live_before
    assert gate.check_commit(state, gate.sha256_file(live_manifest), "marker-1")["ready"] is False


def test_same_blogger_sources_cannot_be_labeled_consensus(tmp_path: Path) -> None:
    validate = load("validate_preview")
    preview = tmp_path / "preview"
    make_preview(preview, ("同一博主", "同一博主"))
    write_json(preview / "build-metrics.json", validate.derive_metrics(preview))

    with pytest.raises(validate.PreviewValidationError, match="two distinct bloggers"):
        validate.validate_preview(preview)


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_skill_explicitly_keeps_semantically_overlapping_layer2_cards() -> None:
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "观点重合不删除二层卡" in skill
    assert "相似度分数直接合并" in skill
