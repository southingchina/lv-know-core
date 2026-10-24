import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import layer2_cards
import layer2_source
import validate_layer2_stage


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def run_isolated_pipeline(tmp_path: Path) -> dict:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    definitions = [
        ("short-001", "short_video", None, None, "先别急。这个问题要换个角度。"),
        ("lesson-01", "long_form", "course-a", 1, "第一节定义 GEO。"),
        ("lesson-02", "long_form", "course-a", 2, "第二节沿用上一节定义。"),
        ("lesson-03", "long_form", "course-a", 3, "第三节给出应用。"),
        ("article-a", "article_or_interview", None, None, "相同正文。"),
        ("article-b", "article_or_interview", None, None, "相同正文。"),
    ]
    spec_units = []
    for unit_id, source_type, group_id, order, raw in definitions:
        path = inputs / f"{unit_id}.md"
        path.write_text(raw, encoding="utf-8")
        spec_units.append({
            "unit_id": unit_id,
            "card_id": unit_id,
            "blogger": "测试博主",
            "source_title": unit_id,
            "path": str(path),
            "locator": "whole-file",
            "source_type": source_type,
            "primary_theme": "02",
            "group_id": group_id,
            "order": order,
            "start_line": None,
            "end_line": None,
        })
    run = tmp_path / "run"
    spec_path = run / "source-spec.json"
    layer2_source.write_json(spec_path, {"schema_version": 2, "units": spec_units})
    inventory_path = layer2_source.build_inventory(spec_path, run)
    context_map = run / "context-map.json"
    layer2_source.write_json(context_map, {
        "schema_version": 2,
        "groups": [{
            "group_id": "course-a",
            "overview": "从定义到应用",
            "glossary": [{"term": "GEO", "meaning": "课程内定义"}],
            "units": [
                {"unit_id": "lesson-01", "summary": "定义"},
                {"unit_id": "lesson-02", "summary": "展开"},
                {"unit_id": "lesson-03", "summary": "应用"},
            ],
        }],
    })
    layer2_source.build_context_packets(
        inventory_path,
        context_map,
        run / "context-packets.jsonl",
    )
    extraction_rows = []
    for unit_id, source_type, group_id, order, raw in definitions:
        if unit_id == "lesson-03":
            continue
        row = {
            "unit_id": unit_id,
            "card_title": unit_id,
            "additional_tags": [],
            "enrichment_status": "complete",
            "hook": None,
            "sections": [{
                "heading": "内容主旨",
                "paragraphs": [f"{unit_id} 的主旨。"],
                "items": [],
            }],
            "calibrated_transcript": None,
        }
        if unit_id == "short-001":
            row["hook"] = {"quote": "先别急。", "function": "直接打断惯性"}
        extraction_rows.append(row)
    extraction_path = run / "extraction-results.jsonl"
    write_jsonl(extraction_path, extraction_rows)
    layer2_cards.render_stage(inventory_path, extraction_path, run)
    return validate_layer2_stage.validate_layer2_stage(run)


def remove_one_manifest_row(tmp_path: Path) -> None:
    manifest_path = tmp_path / "run" / "staged-layer2-manifest.json"
    manifest = layer2_source.read_json(manifest_path)
    manifest["cards"] = manifest["cards"][:-1]
    layer2_source.write_json(manifest_path, manifest)


def test_complete_isolated_layer2_run(tmp_path: Path) -> None:
    result = run_isolated_pipeline(tmp_path)

    assert result["status"] == "pass"
    assert result["counts"] == {
        "input_units": 6,
        "staged_cards": 6,
        "existing_units": 0,
        "failures": 0,
        "partial_enrichment": 1,
    }
    assert result["short_video_split_errors"] == []
    assert result["raw_transcript_mismatches"] == []
    assert result["long_form_context_errors"] == []


def test_missing_card_fails_accounting(tmp_path: Path) -> None:
    run_isolated_pipeline(tmp_path)
    remove_one_manifest_row(tmp_path)
    with pytest.raises(validate_layer2_stage.Layer2ValidationError, match="unaccounted input unit"):
        validate_layer2_stage.validate_layer2_stage(tmp_path / "run")

    report = layer2_source.read_json(tmp_path / "run" / "reports" / "layer2-validation.json")
    assert report["status"] == "fail"
    assert "unaccounted input unit" in report["error"]


def test_tampered_hook_fails_with_report(tmp_path: Path) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    manifest = layer2_source.read_json(run / "staged-layer2-manifest.json")
    short_card = next(row for row in manifest["cards"] if row["unit_id"] == "short-001")
    card_path = Path(short_card["output_path"])
    card_path.write_text(
        card_path.read_text(encoding="utf-8").replace("> 先别急。", "> 换个角度。"),
        encoding="utf-8",
    )
    with pytest.raises(validate_layer2_stage.Layer2ValidationError, match="hook is not opening raw prefix"):
        validate_layer2_stage.validate_layer2_stage(run)

    report = layer2_source.read_json(run / "reports" / "layer2-validation.json")
    assert report["status"] == "fail"
    assert "hook is not opening raw prefix" in report["error"]


def test_raw_mismatch_keeps_detailed_failure_report(tmp_path: Path) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    manifest = layer2_source.read_json(run / "staged-layer2-manifest.json")
    short_card = next(row for row in manifest["cards"] if row["unit_id"] == "short-001")
    card_path = Path(short_card["output_path"])
    card_path.write_text(
        card_path.read_text(encoding="utf-8").replace("这个问题", "这个答案"),
        encoding="utf-8",
    )

    with pytest.raises(validate_layer2_stage.Layer2ValidationError, match="raw transcript mismatch"):
        validate_layer2_stage.validate_layer2_stage(run)

    report = layer2_source.read_json(run / "reports" / "layer2-validation.json")
    assert report["status"] == "fail"
    assert report["raw_transcript_mismatches"] == ["short-001"]


def test_malformed_quoted_frontmatter_replaces_pass_report(tmp_path: Path) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    report_path = run / "reports" / "layer2-validation.json"
    assert layer2_source.read_json(report_path)["status"] == "pass"

    manifest = layer2_source.read_json(run / "staged-layer2-manifest.json")
    short_card = next(row for row in manifest["cards"] if row["unit_id"] == "short-001")
    card_path = Path(short_card["output_path"])
    card_path.write_text(
        card_path.read_text(encoding="utf-8").replace(
            "id: short-001", 'id: "unterminated'
        ),
        encoding="utf-8",
    )

    with pytest.raises(validate_layer2_stage.Layer2ValidationError, match="invalid quoted frontmatter"):
        validate_layer2_stage.validate_layer2_stage(run)

    report = layer2_source.read_json(report_path)
    assert report["status"] == "fail"
    assert "invalid quoted frontmatter" in report["error"]

    cli = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_layer2_stage.py"), "--run-dir", str(run)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert cli.returncode == 1
    assert "invalid quoted frontmatter" in cli.stdout
    assert layer2_source.read_json(report_path)["status"] == "fail"


def test_long_form_packet_without_current_fails_with_report(tmp_path: Path) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    packets_path = run / "context-packets.jsonl"
    packets = [json.loads(line) for line in packets_path.read_text(encoding="utf-8").splitlines()]
    next(row for row in packets if row["unit_id"] == "lesson-01")["current"] = None
    write_jsonl(packets_path, packets)

    with pytest.raises(validate_layer2_stage.Layer2ValidationError, match="long-form context invalid"):
        validate_layer2_stage.validate_layer2_stage(run)

    report = layer2_source.read_json(run / "reports" / "layer2-validation.json")
    assert report["status"] == "fail"
    assert "long-form context invalid" in report["error"]


def test_invalid_existing_unit_identity_mapping_fails_validation(
    tmp_path: Path,
) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    manifest_path = run / "staged-layer2-manifest.json"
    manifest = layer2_source.read_json(manifest_path)
    card = manifest["cards"].pop()
    unit = next(
        row
        for row in layer2_source.read_json(run / "source-inventory.json")["units"]
        if row["unit_id"] == card["unit_id"]
    )
    manifest["existing_units"].append({
        "unit_id": unit["unit_id"],
        "id": card["id"],
        "relative_path": card["relative_path"],
        "source_path": unit["source_path"],
        "source_sha256": unit["source_sha256"],
        "source_locator": unit["source_locator"],
        "source_unit_sha256": unit["source_unit_sha256"],
        "idempotency_key": "0" * 64,
    })
    layer2_source.write_json(manifest_path, manifest)

    with pytest.raises(
        validate_layer2_stage.Layer2ValidationError,
        match="existing unit identity mismatch",
    ):
        validate_layer2_stage.validate_layer2_stage(run)


def test_original_source_hash_drift_replaces_prior_pass_report(
    tmp_path: Path,
) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    report_path = run / "reports" / "layer2-validation.json"
    assert layer2_source.read_json(report_path)["status"] == "pass"
    unit = layer2_source.read_json(run / "source-inventory.json")["units"][0]
    Path(unit["source_path"]).write_text("原文件已经变化。", encoding="utf-8")

    with pytest.raises(
        validate_layer2_stage.Layer2ValidationError,
        match="source file hash mismatch",
    ):
        validate_layer2_stage.validate_layer2_stage(run)

    report = layer2_source.read_json(report_path)
    assert report["status"] == "fail"
    assert "source file hash mismatch" in report["error"]


def test_raw_copy_and_card_that_drift_together_still_fail_original_check(
    tmp_path: Path,
) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    inventory = layer2_source.read_json(run / "source-inventory.json")
    unit = next(row for row in inventory["units"] if row["unit_id"] == "article-a")
    Path(unit["raw_text_path"]).write_text("共同漂移。", encoding="utf-8")
    manifest = layer2_source.read_json(run / "staged-layer2-manifest.json")
    card = next(row for row in manifest["cards"] if row["unit_id"] == "article-a")
    card_path = Path(card["output_path"])
    card_path.write_text(
        card_path.read_text(encoding="utf-8").replace("相同正文。", "共同漂移。"),
        encoding="utf-8",
    )

    with pytest.raises(
        validate_layer2_stage.Layer2ValidationError,
        match="raw source unit mismatch",
    ):
        validate_layer2_stage.validate_layer2_stage(run)

    report = layer2_source.read_json(run / "reports" / "layer2-validation.json")
    assert report["status"] == "fail"
    assert "raw source unit mismatch" in report["error"]


def test_tampered_identity_everywhere_is_recomputed_from_four_fields(
    tmp_path: Path,
) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    inventory_path = run / "source-inventory.json"
    inventory = layer2_source.read_json(inventory_path)
    unit = next(row for row in inventory["units"] if row["unit_id"] == "article-a")
    original_key = unit["idempotency_key"]
    unit["idempotency_key"] = "0" * 64
    layer2_source.write_json(inventory_path, inventory)
    manifest_path = run / "staged-layer2-manifest.json"
    manifest = layer2_source.read_json(manifest_path)
    card = next(row for row in manifest["cards"] if row["unit_id"] == "article-a")
    card["idempotency_key"] = "0" * 64
    layer2_source.write_json(manifest_path, manifest)
    card_path = Path(card["output_path"])
    card_path.write_text(
        card_path.read_text(encoding="utf-8").replace(original_key, "0" * 64),
        encoding="utf-8",
    )

    with pytest.raises(
        validate_layer2_stage.Layer2ValidationError,
        match="idempotency key mismatch",
    ):
        validate_layer2_stage.validate_layer2_stage(run)


@pytest.mark.parametrize(
    ("unit_id", "field", "value"),
    [
        ("lesson-02", "previous", {"unit_id": "lesson-01", "summary": "错误摘要"}),
        (
            "lesson-02",
            "next",
            {
                "unit_id": "lesson-03",
                "summary": "应用",
                "raw_text_path": "D:/wrong/lesson-03.txt",
            },
        ),
        (
            "lesson-01",
            "global_context",
            {"overview": "错误总览", "glossary": []},
        ),
    ],
)
def test_complete_long_form_packet_must_match_inventory_and_context_map(
    tmp_path: Path,
    unit_id: str,
    field: str,
    value: object,
) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    packets_path = run / "context-packets.jsonl"
    packets = [
        json.loads(line)
        for line in packets_path.read_text(encoding="utf-8").splitlines()
    ]
    next(row for row in packets if row["unit_id"] == unit_id)[field] = value
    write_jsonl(packets_path, packets)

    with pytest.raises(
        validate_layer2_stage.Layer2ValidationError,
        match="long-form context invalid",
    ):
        validate_layer2_stage.validate_layer2_stage(run)


def test_partial_long_form_packet_cannot_carry_complete_context(
    tmp_path: Path,
) -> None:
    run_isolated_pipeline(tmp_path)
    run = tmp_path / "run"
    packets_path = run / "context-packets.jsonl"
    packets = [
        json.loads(line)
        for line in packets_path.read_text(encoding="utf-8").splitlines()
    ]
    packet = next(row for row in packets if row["unit_id"] == "lesson-02")
    packet["context_status"] = "partial"
    write_jsonl(packets_path, packets)

    with pytest.raises(
        validate_layer2_stage.Layer2ValidationError,
        match="long-form context invalid",
    ):
        validate_layer2_stage.validate_layer2_stage(run)
