import hashlib
import html
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import layer2_cards
import layer2_source


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_unit(tmp_path: Path, raw: str, **overrides: object) -> dict:
    units = tmp_path / "run" / "source-units"
    units.mkdir(parents=True, exist_ok=True)
    raw_path = units / f"{overrides.get('unit_id', 'short-001')}.txt"
    raw_path.write_text(raw, encoding="utf-8")
    unit = {
        "unit_id": "short-001",
        "card_id": "short-001",
        "blogger": "测试博主",
        "source_title": "测试标题",
        "source_path": str(tmp_path / "input.md"),
        "source_sha256": "a" * 64,
        "source_locator": "whole-file",
        "source_unit_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "idempotency_key": "b" * 64,
        "source_type": "short_video",
        "primary_theme": "02",
        "group_id": None,
        "order": None,
        "raw_text_path": str(raw_path),
    }
    unit.update(overrides)
    return unit


def render_case(tmp_path: Path, raw: str, extraction: dict | None) -> tuple[dict, str]:
    run = tmp_path / "run"
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [make_unit(tmp_path, raw)]})
    extraction_path = None
    if extraction is not None:
        extraction_path = run / "extraction-results.jsonl"
        extraction_path.write_text(json.dumps(extraction, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path = layer2_cards.render_stage(inventory_path, extraction_path, run)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    card = Path(manifest["cards"][0]["output_path"]).read_text(encoding="utf-8")
    return manifest, card


def raw_transcript_from(card: str) -> str:
    start = '<pre data-lv-know="raw-transcript">'
    return html.unescape(card.split(start, 1)[1].split("</pre>", 1)[0])


def test_minimal_card_embeds_complete_raw_transcript(tmp_path: Path) -> None:
    manifest, card = render_case(
        tmp_path,
        raw="嗯，先别急。\n第二句重复，第二句重复。",
        extraction=None,
    )

    assert manifest["cards"][0]["enrichment_status"] == "partial"
    assert "<summary>完整原始口播稿</summary>" in card
    assert raw_transcript_from(card) == "嗯，先别急。\n第二句重复，第二句重复。"
    assert "### 观点提炼" not in card
    assert "### 说服路径" not in card


def test_optional_sections_render_only_when_present(tmp_path: Path) -> None:
    extraction = {
        "unit_id": "short-001",
        "card_title": "测试标题",
        "additional_tags": [],
        "enrichment_status": "complete",
        "hook": {"quote": "先别急。", "function": "直接打断惯性"},
        "sections": [{
            "heading": "内容主旨",
            "paragraphs": ["提醒观众先判断再行动。"],
            "items": [],
        }],
        "calibrated_transcript": None,
    }
    _, card = render_case(tmp_path, raw="先别急。后面展开。", extraction=extraction)

    assert "## 内容主旨" in card
    assert "### 开场钩子" in card
    assert "## 案例" not in card


def test_hook_must_be_real_opening_prefix(tmp_path: Path) -> None:
    extraction = {
        "unit_id": "short-001",
        "card_title": "测试标题",
        "additional_tags": [],
        "enrichment_status": "complete",
        "hook": {"quote": "后文金句。", "function": "错误地取了后文"},
        "sections": [],
        "calibrated_transcript": None,
    }
    with pytest.raises(ValueError, match="hook is not an opening prefix"):
        render_case(tmp_path, raw="真正开场。后文金句。", extraction=extraction)


def test_raw_transcript_is_html_escaped_without_any_loss(tmp_path: Path) -> None:
    raw = '开场 <script>alert("x")</script> & 重复\n最后一句'
    _, card = render_case(tmp_path, raw=raw, extraction=None)

    assert "<script>" not in card
    assert raw_transcript_from(card) == raw


def test_partial_extraction_without_optional_fields_still_renders(tmp_path: Path) -> None:
    manifest, card = render_case(
        tmp_path,
        raw="原始内容。",
        extraction={"unit_id": "short-001", "enrichment_status": "partial"},
    )

    assert manifest["cards"][0]["enrichment_status"] == "partial"
    assert "# 测试标题" in card
    assert "完整原始口播稿" in card


def test_unknown_extraction_unit_is_rejected_before_cards_are_published(tmp_path: Path) -> None:
    run = tmp_path / "run"
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [make_unit(tmp_path, "原始内容。")]})
    extraction_path = run / "extraction-results.jsonl"
    extraction_path.write_text(json.dumps({"unit_id": "missing-001"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unknown units"):
        layer2_cards.render_stage(inventory_path, extraction_path, run)

    assert not (run / "layer2-cards").exists()
    assert not (run / "staged-layer2-manifest.json").exists()


def test_case_insensitive_duplicate_card_id_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    first = make_unit(tmp_path, "第一个。", unit_id="one", card_id="Card-1")
    second = make_unit(tmp_path, "第二个。", unit_id="two", card_id="card-1")
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [first, second]})

    with pytest.raises(ValueError, match="duplicate card_id"):
        layer2_cards.render_stage(inventory_path, None, run)

    assert not (run / "layer2-cards").exists()


def test_invalid_later_extraction_does_not_silently_publish_earlier_card(tmp_path: Path) -> None:
    run = tmp_path / "run"
    first = make_unit(tmp_path, "第一个。", unit_id="one", card_id="one")
    second = make_unit(tmp_path, "第二个。", unit_id="two", card_id="two")
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [first, second]})
    extraction_path = run / "extraction-results.jsonl"
    extraction_path.write_text(
        json.dumps({
            "unit_id": "two",
            "additional_tags": ["GEO/不是正式标签"],
            "enrichment_status": "complete",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported additional tag"):
        layer2_cards.render_stage(inventory_path, extraction_path, run)

    assert not (run / "layer2-cards").exists()
    assert not (run / "staged-layer2-manifest.json").exists()


@pytest.mark.parametrize(
    "blogger",
    ["..", "nested\\blogger", "CON", "尾空格 ", "尾点."],
)
def test_unsafe_blogger_is_rejected_before_stage_creation(
    tmp_path: Path, blogger: str
) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "原始内容。", blogger=blogger)
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})

    with pytest.raises(ValueError, match="invalid blogger"):
        layer2_cards.render_stage(inventory_path, None, run)

    assert not (run / "layer2-cards").exists()
    assert not list(run.glob(".layer2-cards-stage-*"))


def test_absolute_blogger_cannot_write_outside_run_dir(tmp_path: Path) -> None:
    run = tmp_path / "run"
    outside = tmp_path / "outside"
    unit = make_unit(tmp_path, "原始内容。", blogger=str(outside))
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})

    with pytest.raises(ValueError, match="invalid blogger"):
        layer2_cards.render_stage(inventory_path, None, run)

    assert not outside.exists()
    assert not (run / "layer2-cards").exists()


def write_manifest(path: Path, rows: list[dict]) -> Path:
    write_json(path, rows)
    return path


def test_exact_existing_identity_is_accounted_without_rendering_card(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "原始内容。")
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})
    existing_manifest = write_manifest(
        tmp_path / "formal-manifest.json",
        [{
            "id": "existing-card",
            "relative_path": "01-原子知识卡/测试博主/existing-card.md",
            "idempotency_key": unit["idempotency_key"],
        }],
    )

    result = layer2_cards.render_stage(
        inventory_path, None, run, existing_manifest
    )
    manifest = layer2_source.read_json(result)

    assert manifest["cards"] == []
    assert manifest["existing_units"] == [{
        "unit_id": unit["unit_id"],
        "id": "existing-card",
        "relative_path": "01-原子知识卡/测试博主/existing-card.md",
        "source_path": unit["source_path"],
        "source_sha256": unit["source_sha256"],
        "source_locator": unit["source_locator"],
        "source_unit_sha256": unit["source_unit_sha256"],
        "idempotency_key": unit["idempotency_key"],
    }]
    assert not list((run / "layer2-cards").rglob("*.md"))


def test_existing_manifest_derives_identity_only_from_complete_tuple(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "原始内容。")
    unit["idempotency_key"] = layer2_source.make_identity(
        unit["source_path"],
        unit["source_sha256"],
        unit["source_locator"],
        unit["source_unit_sha256"],
    )
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})
    existing_manifest = write_manifest(
        tmp_path / "formal-manifest.json",
        [{
            "id": "existing-card",
            "relative_path": "01-原子知识卡/测试博主/existing-card.md",
            "source_path": unit["source_path"],
            "source_sha256": unit["source_sha256"],
            "source_locator": unit["source_locator"],
            "source_unit_sha256": unit["source_unit_sha256"],
        }],
    )

    result = layer2_cards.render_stage(
        inventory_path, None, run, existing_manifest
    )

    assert layer2_source.read_json(result)["cards"] == []


def test_same_bytes_at_different_path_or_locator_are_not_existing_units(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "相同正文。")
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})
    other_path = tmp_path / "other.md"
    existing_manifest = write_manifest(
        tmp_path / "formal-manifest.json",
        [{
            "id": "other-card",
            "relative_path": "01-原子知识卡/测试博主/other-card.md",
            "source_path": str(other_path),
            "source_sha256": unit["source_sha256"],
            "source_locator": "other-locator",
            "source_unit_sha256": unit["source_unit_sha256"],
        }],
    )

    result = layer2_cards.render_stage(
        inventory_path, None, run, existing_manifest
    )
    manifest = layer2_source.read_json(result)

    assert [row["unit_id"] for row in manifest["cards"]] == [unit["unit_id"]]
    assert manifest["existing_units"] == []


def test_staged_card_id_collision_with_different_existing_identity_fails(
    tmp_path: Path,
) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "原始内容。", card_id="same-card")
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})
    existing_manifest = write_manifest(
        tmp_path / "formal-manifest.json",
        [{
            "id": "same-card",
            "relative_path": "01-原子知识卡/测试博主/same-card.md",
            "idempotency_key": "c" * 64,
        }],
    )

    with pytest.raises(ValueError, match="existing card_id collision"):
        layer2_cards.render_stage(
            inventory_path, None, run, existing_manifest
        )

    assert not (run / "layer2-cards").exists()
    assert not (run / "staged-layer2-manifest.json").exists()


def test_old_manifest_row_without_v2_identity_never_matches(tmp_path: Path) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "原始内容。", card_id="new-card")
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})
    existing_manifest = write_manifest(
        tmp_path / "formal-manifest.json",
        [{
            "id": "legacy-card",
            "relative_path": "01-原子知识卡/测试博主/legacy-card.md",
            "source_sha256": unit["source_sha256"],
        }],
    )

    result = layer2_cards.render_stage(
        inventory_path, None, run, existing_manifest
    )
    manifest = layer2_source.read_json(result)

    assert len(manifest["cards"]) == 1
    assert manifest["existing_units"] == []


def test_research_report_is_rejected_before_generic_staging(tmp_path: Path) -> None:
    run = tmp_path / "run"
    unit = make_unit(
        tmp_path,
        "研究报告正文。",
        source_type="research_report",
    )
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})

    with pytest.raises(
        ValueError,
        match=r"research-report-contract\.md.*research-report-card-template\.md",
    ):
        layer2_cards.render_stage(inventory_path, None, run)

    assert not (run / "layer2-cards").exists()
    assert not (run / "staged-layer2-manifest.json").exists()
    assert not list(run.glob(".layer2-cards-stage-*"))


@pytest.mark.parametrize(
    "hook",
    [
        {"quote": "", "function": "建立悬念"},
        {"quote": "先别急。", "function": "   "},
    ],
)
def test_blank_hook_fields_are_rejected(
    tmp_path: Path, hook: dict[str, str]
) -> None:
    extraction = {
        "unit_id": "short-001",
        "enrichment_status": "complete",
        "hook": hook,
        "sections": [],
    }

    with pytest.raises(ValueError, match="blank hook"):
        render_case(tmp_path, raw="先别急。后面展开。", extraction=extraction)


def test_blank_section_heading_is_rejected(tmp_path: Path) -> None:
    extraction = {
        "unit_id": "short-001",
        "enrichment_status": "complete",
        "sections": [{
            "heading": " ",
            "paragraphs": ["有正文"],
            "items": [],
        }],
    }

    with pytest.raises(ValueError, match="blank section heading"):
        render_case(tmp_path, raw="原始内容。", extraction=extraction)


@pytest.mark.parametrize(
    "item",
    [
        "",
        "   ",
        {"label": "", "text": "正文"},
        {"label": "标签", "text": "   "},
    ],
)
def test_blank_optional_list_item_is_rejected(
    tmp_path: Path, item: object
) -> None:
    extraction = {
        "unit_id": "short-001",
        "enrichment_status": "complete",
        "sections": [{
            "heading": "内容主旨",
            "paragraphs": [],
            "items": [item],
        }],
    }

    with pytest.raises(ValueError, match="blank .*item"):
        render_case(tmp_path, raw="原始内容。", extraction=extraction)


@pytest.mark.parametrize("card_id", ["..", "nested\\card", "CON", "尾空格 ", "尾点."])
def test_unsafe_card_id_is_rejected_before_stage_creation(
    tmp_path: Path, card_id: str
) -> None:
    run = tmp_path / "run"
    unit = make_unit(tmp_path, "原始内容。", card_id=card_id)
    inventory_path = run / "source-inventory.json"
    write_json(inventory_path, {"schema_version": 2, "units": [unit]})

    with pytest.raises(ValueError, match="invalid card_id"):
        layer2_cards.render_stage(inventory_path, None, run)

    assert not (run / "layer2-cards").exists()
    assert not list(run.glob(".layer2-cards-stage-*"))
