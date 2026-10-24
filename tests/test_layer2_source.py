import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import layer2_source


def make_inventory(tmp_path: Path, unit_types: list[str]) -> Path:
    units = []
    for index, source_type in enumerate(unit_types, start=1):
        unit_id = f"lesson-{index:02d}" if source_type == "long_form" else f"short-{index:02d}"
        raw_path = tmp_path / f"{unit_id}.txt"
        raw_path.write_text(f"{unit_id} 原文", encoding="utf-8")
        units.append({
            "unit_id": unit_id,
            "source_type": source_type,
            "group_id": "course-a" if source_type == "long_form" else None,
            "order": index if source_type == "long_form" else None,
            "raw_text_path": str(raw_path),
        })
    path = tmp_path / "source-inventory.json"
    layer2_source.write_json(path, {"schema_version": 2, "units": units})
    return path


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_context_map(tmp_path: Path, groups: list[dict]) -> Path:
    path = tmp_path / "context-map.json"
    layer2_source.write_json(path, {"schema_version": 2, "groups": groups})
    return path


def context_group(group_id: str, unit_ids: list[str]) -> dict:
    return {
        "group_id": group_id,
        "overview": "课程整体路线",
        "glossary": [],
        "units": [
            {"unit_id": unit_id, "summary": f"{unit_id} 摘要"}
            for unit_id in unit_ids
        ],
    }


def packet_for_unit(path: Path, unit_id: str) -> dict:
    return next(packet for packet in read_jsonl(path) if packet["unit_id"] == unit_id)


def test_short_video_packet_has_only_current_unit(tmp_path: Path) -> None:
    inventory = make_inventory(tmp_path, ["short_video"])
    output = tmp_path / "context-packets.jsonl"
    layer2_source.build_context_packets(inventory, None, output)
    packet = read_jsonl(output)[0]

    assert packet["mode"] == "whole_source"
    assert packet["previous"] is None
    assert packet["next"] is None
    assert packet["global_context"] is None


def test_long_form_packet_carries_global_previous_and_next(tmp_path: Path) -> None:
    inventory = make_inventory(tmp_path, ["long_form", "long_form", "long_form"])
    context_map = tmp_path / "context-map.json"
    layer2_source.write_json(context_map, {
        "schema_version": 2,
        "groups": [{
            "group_id": "course-a",
            "overview": "三节课的整体路线",
            "glossary": [{"term": "GEO", "meaning": "AI 搜索优化"}],
            "units": [
                {"unit_id": "lesson-01", "summary": "第一节"},
                {"unit_id": "lesson-02", "summary": "第二节"},
                {"unit_id": "lesson-03", "summary": "第三节"},
            ],
        }],
    })
    output = tmp_path / "context-packets.jsonl"
    layer2_source.build_context_packets(inventory, context_map, output)
    packets = read_jsonl(output)
    middle = packets[1]

    assert middle["mode"] == "long_form_context"
    assert middle["global_context"]["overview"] == "三节课的整体路线"
    assert middle["previous"]["unit_id"] == "lesson-01"
    assert middle["current"]["unit_id"] == "lesson-02"
    assert middle["next"]["unit_id"] == "lesson-03"
    assert middle["context_status"] == "complete"


def test_missing_long_form_map_falls_back_without_losing_current(tmp_path: Path) -> None:
    inventory = make_inventory(tmp_path, ["long_form"])
    output = tmp_path / "context-packets.jsonl"
    layer2_source.build_context_packets(inventory, None, output)
    packet = read_jsonl(output)[0]

    assert packet["context_status"] == "partial"
    assert packet["current"]["raw_text_path"]


def test_invalid_inventory_schema_fails_before_context_output(tmp_path: Path) -> None:
    inventory = make_inventory(tmp_path, ["short_video"])
    payload = layer2_source.read_json(inventory)
    payload["schema_version"] = 999
    layer2_source.write_json(inventory, payload)
    output = tmp_path / "context-packets.jsonl"
    output.write_text('{"existing": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported source-inventory schema"):
        layer2_source.build_context_packets(inventory, None, output)

    assert output.read_text(encoding="utf-8") == '{"existing": true}\n'


@pytest.mark.parametrize(
    ("map_unit_ids", "affected_unit_id"),
    [
        (["lesson-01", "lesson-03"], "lesson-02"),
        (["lesson-01", "lesson-03"], "lesson-01"),
    ],
    ids=["missing-current", "missing-adjacent"],
)
def test_incomplete_long_form_context_map_falls_back_without_context(
    tmp_path: Path, map_unit_ids: list[str], affected_unit_id: str
) -> None:
    inventory = make_inventory(tmp_path, ["long_form", "long_form", "long_form"])
    context_map = write_context_map(
        tmp_path, [context_group("course-a", map_unit_ids)]
    )
    output = tmp_path / "context-packets.jsonl"

    layer2_source.build_context_packets(inventory, context_map, output)
    packet = packet_for_unit(output, affected_unit_id)

    assert packet["context_status"] == "partial"
    assert packet["current"]["raw_text_path"]
    assert packet["global_context"] is None
    assert packet["previous"] is None
    assert packet["next"] is None


@pytest.mark.parametrize("invalid_group", ["missing-overview", "missing-summary", "wrong-order"])
def test_noncomplete_long_form_context_map_falls_back_without_context(
    tmp_path: Path, invalid_group: str
) -> None:
    inventory = make_inventory(tmp_path, ["long_form", "long_form"])
    group = context_group("course-a", ["lesson-01", "lesson-02"])
    if invalid_group == "missing-overview":
        del group["overview"]
    elif invalid_group == "missing-summary":
        del group["units"][1]["summary"]
    else:
        group["units"].reverse()
    context_map = write_context_map(tmp_path, [group])
    output = tmp_path / "context-packets.jsonl"

    layer2_source.build_context_packets(inventory, context_map, output)
    packet = packet_for_unit(output, "lesson-01")

    assert packet["context_status"] == "partial"
    assert packet["current"]["raw_text_path"]
    assert packet["global_context"] is None
    assert packet["previous"] is None
    assert packet["next"] is None


def test_cross_group_context_map_member_falls_back_without_context(
    tmp_path: Path,
) -> None:
    inventory = make_inventory(tmp_path, ["long_form", "long_form"])
    payload = layer2_source.read_json(inventory)
    payload["units"][1]["group_id"] = "course-b"
    payload["units"][1]["order"] = 1
    layer2_source.write_json(inventory, payload)
    context_map = write_context_map(
        tmp_path, [context_group("course-a", ["lesson-01", "lesson-02"])]
    )
    output = tmp_path / "context-packets.jsonl"

    layer2_source.build_context_packets(inventory, context_map, output)
    packet = packet_for_unit(output, "lesson-01")

    assert packet["context_status"] == "partial"
    assert packet["current"]["raw_text_path"]
    assert packet["global_context"] is None
    assert packet["previous"] is None
    assert packet["next"] is None


@pytest.mark.parametrize(
    ("units", "error"),
    [
        ({}, "source-inventory units must be an array"),
        ([
            {
                "unit_id": "duplicate",
                "source_type": "short_video",
                "raw_text_path": "one.txt",
            },
            {
                "unit_id": "duplicate",
                "source_type": "short_video",
                "raw_text_path": "two.txt",
            },
        ], "duplicate inventory unit_id"),
    ],
    ids=["units-not-array", "duplicate-unit-id"],
)
def test_invalid_inventory_units_hard_fail_before_context_output(
    tmp_path: Path, units: object, error: str
) -> None:
    inventory = tmp_path / "source-inventory.json"
    layer2_source.write_json(inventory, {"schema_version": 2, "units": units})
    output = tmp_path / "context-packets.jsonl"

    with pytest.raises(ValueError, match=error):
        layer2_source.build_context_packets(inventory, None, output)

    assert not output.exists()


def test_duplicate_context_map_unit_id_hard_fails_before_context_output(
    tmp_path: Path,
) -> None:
    inventory = make_inventory(tmp_path, ["long_form"])
    context_map = write_context_map(
        tmp_path, [context_group("course-a", ["lesson-01", "lesson-01"])]
    )
    output = tmp_path / "context-packets.jsonl"

    with pytest.raises(ValueError, match="duplicate context unit_id"):
        layer2_source.build_context_packets(inventory, context_map, output)

    assert not output.exists()


def make_spec(source: Path, **overrides: object) -> dict:
    unit = {
        "unit_id": "unit-001",
        "card_id": "card-001",
        "blogger": "测试博主",
        "source_title": "来源标题",
        "path": str(source),
        "locator": "whole-file",
        "source_type": "short_video",
        "primary_theme": "02",
        "group_id": None,
        "order": None,
        "start_line": None,
        "end_line": None,
    }
    unit.update(overrides)
    return {"schema_version": 2, "units": [unit]}


def write_spec(path: Path, payload: dict) -> Path:
    spec_path = path / "source-spec.json"
    layer2_source.write_json(spec_path, payload)
    return spec_path


def test_inventory_preserves_short_video_verbatim(tmp_path: Path) -> None:
    source = tmp_path / "short.md"
    source.write_bytes("\ufeff第一句。\r\n嗯，第二句重复，第二句重复。".encode("utf-8"))
    spec = {
        "schema_version": 2,
        "units": [{
            "unit_id": "short-001",
            "card_id": "short-001",
            "blogger": "测试博主",
            "source_title": "短视频",
            "path": str(source),
            "locator": "whole-file",
            "source_type": "short_video",
            "primary_theme": "02",
            "group_id": None,
            "order": None,
            "start_line": None,
            "end_line": None,
        }],
    }
    spec_path = tmp_path / "source-spec.json"
    layer2_source.write_json(spec_path, spec)

    inventory_path = layer2_source.build_inventory(spec_path, tmp_path / "run")
    inventory = layer2_source.read_json(inventory_path)
    unit = inventory["units"][0]
    raw = Path(unit["raw_text_path"]).read_text(encoding="utf-8")

    assert raw == "第一句。\n嗯，第二句重复，第二句重复。"
    assert unit["source_type"] == "short_video"
    assert len(unit["source_sha256"]) == 64
    assert len(unit["source_unit_sha256"]) == 64
    assert not (tmp_path / "run" / "source-units" / ".layer2-owner.json").exists()


def test_inventory_retains_line_range_used_for_logical_unit(tmp_path: Path) -> None:
    source = tmp_path / "chapters.md"
    source.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
    spec = make_spec(
        source,
        source_type="long_form",
        group_id=None,
        start_line=2,
        end_line=2,
    )

    inventory_path = layer2_source.build_inventory(
        write_spec(tmp_path, spec), tmp_path / "run"
    )
    unit = layer2_source.read_json(inventory_path)["units"][0]

    assert unit["start_line"] == 2
    assert unit["end_line"] == 2
    assert Path(unit["raw_text_path"]).read_text(encoding="utf-8") == "第二行\n"


def test_same_text_from_different_sources_has_distinct_idempotency_key(tmp_path: Path) -> None:
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("相同正文", encoding="utf-8")
    second.write_text("相同正文", encoding="utf-8")

    left = layer2_source.make_identity(
        first.resolve(),
        layer2_source.sha256_file(first),
        "whole-file",
        layer2_source.sha256_text("相同正文"),
    )
    right = layer2_source.make_identity(
        second.resolve(),
        layer2_source.sha256_file(second),
        "whole-file",
        layer2_source.sha256_text("相同正文"),
    )

    assert left != right


@pytest.mark.parametrize("order", [None, 0, -1, "1", 1.0, True])
def test_grouped_long_form_requires_positive_integer_order(
    tmp_path: Path, order: object
) -> None:
    source = tmp_path / "lesson.md"
    source.write_text("课程正文", encoding="utf-8")
    spec = make_spec(
        source,
        source_type="long_form",
        group_id="course-a",
        order=order,
    )

    with pytest.raises(ValueError, match="positive integer order"):
        layer2_source.build_inventory(
            write_spec(tmp_path, spec), tmp_path / "run"
        )


def test_grouped_long_form_order_is_unique_within_group(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("第一节", encoding="utf-8")
    second.write_text("第二节", encoding="utf-8")
    spec = make_spec(
        first,
        unit_id="lesson-01",
        card_id="lesson-01",
        source_type="long_form",
        group_id="course-a",
        order=1,
    )
    spec["units"].append(
        make_spec(
            second,
            unit_id="lesson-02",
            card_id="lesson-02",
            source_type="long_form",
            group_id="course-a",
            order=1,
        )["units"][0]
    )

    with pytest.raises(ValueError, match="duplicate long_form order"):
        layer2_source.build_inventory(
            write_spec(tmp_path, spec), tmp_path / "run"
        )


def test_ungrouped_long_form_without_order_remains_partial(tmp_path: Path) -> None:
    source = tmp_path / "standalone.md"
    source.write_text("独立长文", encoding="utf-8")
    spec = make_spec(
        source,
        source_type="long_form",
        group_id=None,
        order=None,
    )
    run = tmp_path / "run"
    inventory = layer2_source.build_inventory(write_spec(tmp_path, spec), run)

    output = run / "context-packets.jsonl"
    layer2_source.build_context_packets(inventory, None, output)

    packet = read_jsonl(output)[0]
    assert packet["context_status"] == "partial"
    assert packet["global_context"] is None
    assert packet["previous"] is None
    assert packet["next"] is None


def test_casefolded_unit_id_collision_fails_before_any_raw_write(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first source", encoding="utf-8")
    second.write_text("second source", encoding="utf-8")
    spec = make_spec(first, unit_id="Foo", card_id="card-001")
    spec["units"].append(make_spec(second, unit_id="foo", card_id="card-002")["units"][0])

    run_dir = tmp_path / "run"
    with pytest.raises(ValueError, match="duplicate unit_id"):
        layer2_source.build_inventory(write_spec(tmp_path, spec), run_dir)

    assert not (run_dir / "source-units" / "Foo.txt").exists()
    assert not (run_dir / "source-units" / "foo.txt").exists()


@pytest.mark.parametrize("field,value", [
    ("unit_id", "duplicate"),
    ("card_id", "duplicate"),
])
def test_duplicate_ids_are_rejected(tmp_path: Path, field: str, value: str) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    spec = make_spec(source)
    duplicate = make_spec(source, unit_id="unit-002", card_id="card-002")["units"][0]
    duplicate[field] = value
    spec["units"][0][field] = value
    spec["units"].append(duplicate)

    with pytest.raises(ValueError, match=f"duplicate {field}"):
        layer2_source.build_inventory(write_spec(tmp_path, spec), tmp_path / "run")


@pytest.mark.parametrize("field,value", [
    ("unit_id", "bad/name"),
    ("card_id", "CON"),
    ("blogger", "LPT9"),
    ("unit_id", "unit. "),
    ("card_id", "."),
    ("blogger", ""),
])
def test_windows_unsafe_path_segments_are_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")

    with pytest.raises(ValueError, match=f"invalid {field}"):
        layer2_source.build_inventory(
            write_spec(tmp_path, make_spec(source, **{field: value})),
            tmp_path / "run",
        )


@pytest.mark.parametrize("overrides,error", [
    ({"source_type": "unsupported"}, "unsupported source_type"),
    ({"primary_theme": "09"}, "invalid primary_theme"),
    ({"path": "missing.md"}, "missing.md"),
    ({"start_line": 2, "end_line": 1}, "invalid line range"),
])
def test_invalid_source_metadata_is_rejected(
    tmp_path: Path, overrides: dict[str, object], error: str
) -> None:
    source = tmp_path / "source.md"
    source.write_text("第一行\n第二行", encoding="utf-8")
    if overrides.get("path") == "missing.md":
        overrides = {**overrides, "path": str(tmp_path / "missing.md")}

    with pytest.raises((ValueError, FileNotFoundError), match=error):
        layer2_source.build_inventory(
            write_spec(tmp_path, make_spec(source, **overrides)),
            tmp_path / "run",
        )


def test_empty_source_unit_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "empty.md"
    source.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="empty source unit"):
        layer2_source.build_inventory(
            write_spec(tmp_path, make_spec(source)), tmp_path / "run"
        )


def test_context_map_is_created_once_and_not_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    spec_path = write_spec(tmp_path, make_spec(source))
    created_run = tmp_path / "created-run"

    layer2_source.build_inventory(spec_path, created_run)
    context_map = created_run / "context-map.json"
    assert layer2_source.read_json(context_map) == {
        "schema_version": 2,
        "groups": [],
    }

    existing_run = tmp_path / "existing-run"
    existing_run.mkdir()
    existing_context_map = existing_run / "context-map.json"
    layer2_source.write_json(existing_context_map, {"sentinel": True})
    layer2_source.build_inventory(spec_path, existing_run)
    assert layer2_source.read_json(existing_context_map) == {"sentinel": True}


def test_inventory_cli_prints_json_path_and_writes_inventory(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    spec_path = write_spec(tmp_path, make_spec(source))
    run_dir = tmp_path / "run"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "layer2_source.py"),
            "inventory",
            "--spec",
            str(spec_path),
            "--run-dir",
            str(run_dir),
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )

    output = json.loads(result.stdout)
    inventory_path = Path(output["inventory"])
    assert inventory_path == (run_dir / "source-inventory.json").resolve()
    assert layer2_source.read_json(inventory_path)["count"] == 1


@pytest.mark.parametrize("segment", [
    "COM¹",
    "COM².txt",
    "COM³",
    "LPT¹",
    "LPT².md",
    "LPT³",
])
def test_windows_unicode_device_names_fail_before_raw_publication(
    tmp_path: Path, segment: str
) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="invalid unit_id"):
        layer2_source.build_inventory(
            write_spec(tmp_path, make_spec(source, unit_id=segment)), run_dir
        )

    assert not (run_dir / "source-units").exists()


def test_casefolded_card_id_collision_fails_before_raw_publication(
    tmp_path: Path
) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first source", encoding="utf-8")
    second.write_text("second source", encoding="utf-8")
    spec = make_spec(first, unit_id="unit-001", card_id="Card-001")
    spec["units"].append(
        make_spec(second, unit_id="unit-002", card_id="card-001")["units"][0]
    )
    run_dir = tmp_path / "run"

    with pytest.raises(ValueError, match="duplicate card_id"):
        layer2_source.build_inventory(write_spec(tmp_path, spec), run_dir)

    assert not (run_dir / "source-units").exists()


def test_staging_write_failure_cleans_staging_without_publishing_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("first source", encoding="utf-8")
    second.write_text("second source", encoding="utf-8")
    spec = make_spec(first, unit_id="first", card_id="card-001")
    spec["units"].append(
        make_spec(second, unit_id="second", card_id="card-002")["units"][0]
    )
    original_write_text = Path.write_text

    def fail_second_staged_write(path: Path, *args: object, **kwargs: object) -> int:
        if path.name == "second.txt.tmp":
            raise OSError("simulated staged write failure")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_second_staged_write)
    run_dir = tmp_path / "run"
    with pytest.raises(OSError, match="simulated staged write failure"):
        layer2_source.build_inventory(write_spec(tmp_path, spec), run_dir)

    assert not (run_dir / "source-units").exists()
    assert not (run_dir / "source-inventory.json").exists()
    assert not list(run_dir.glob(".layer2-source-stage-*"))


@pytest.mark.parametrize("existing_name", ["source-units", "source-inventory.json"])
def test_existing_final_inventory_outputs_are_never_overwritten(
    tmp_path: Path, existing_name: str
) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    existing = run_dir / existing_name
    if existing_name == "source-units":
        existing.mkdir()
    else:
        existing.write_text('{"old": true}\n', encoding="utf-8")

    with pytest.raises(FileExistsError):
        layer2_source.build_inventory(write_spec(tmp_path, make_spec(source)), run_dir)

    if existing_name == "source-units":
        assert existing.is_dir()
        assert not list(existing.iterdir())
    else:
        assert existing.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not list(run_dir.glob(".layer2-source-stage-*"))


def test_publish_failure_rolls_back_current_transaction_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    run_dir = tmp_path / "run"
    original_rename = layer2_source.os.rename

    def fail_inventory_publish(source_path: object, target_path: object) -> None:
        if Path(target_path) == run_dir / "source-inventory.json":
            raise OSError("simulated inventory publish failure")
        original_rename(source_path, target_path)

    monkeypatch.setattr(layer2_source.os, "rename", fail_inventory_publish)
    with pytest.raises(OSError, match="simulated inventory publish failure"):
        layer2_source.build_inventory(write_spec(tmp_path, make_spec(source)), run_dir)

    assert not (run_dir / "source-units").exists()
    assert not (run_dir / "source-inventory.json").exists()
    assert not list(run_dir.glob(".layer2-source-stage-*"))


def test_foreign_source_units_replacement_survives_publish_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    run_dir = tmp_path / "run"
    source_units = run_dir / "source-units"
    inventory = run_dir / "source-inventory.json"
    original_rename = layer2_source.os.rename

    def replace_then_fail(source_path: object, target_path: object) -> None:
        target = Path(target_path)
        if target == source_units:
            original_rename(source_path, target)
            shutil.rmtree(target)
            target.mkdir()
            (target / "foreign.txt").write_text("foreign", encoding="utf-8")
            return
        if target == inventory:
            raise OSError("simulated inventory publish failure")
        original_rename(source_path, target)

    monkeypatch.setattr(layer2_source.os, "rename", replace_then_fail)
    with pytest.raises(OSError, match="simulated inventory publish failure"):
        layer2_source.build_inventory(write_spec(tmp_path, make_spec(source)), run_dir)

    assert (source_units / "foreign.txt").read_text(encoding="utf-8") == "foreign"
    assert not inventory.exists()
    assert not list(run_dir.glob(".layer2-source-stage-*"))


def test_foreign_inventory_replacement_survives_context_publish_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.md"
    source.write_text("正文", encoding="utf-8")
    run_dir = tmp_path / "run"
    inventory = run_dir / "source-inventory.json"
    context_map = run_dir / "context-map.json"
    original_rename = layer2_source.os.rename

    def replace_inventory_then_fail_context(
        source_path: object, target_path: object
    ) -> None:
        target = Path(target_path)
        if target == inventory:
            original_rename(source_path, target)
            target.write_text('{"foreign": true}\n', encoding="utf-8")
            return
        if target == context_map:
            raise OSError("simulated context publish failure")
        original_rename(source_path, target)

    monkeypatch.setattr(layer2_source.os, "rename", replace_inventory_then_fail_context)
    with pytest.raises(OSError, match="simulated context publish failure"):
        layer2_source.build_inventory(write_spec(tmp_path, make_spec(source)), run_dir)

    assert inventory.read_text(encoding="utf-8") == '{"foreign": true}\n'
    assert not (run_dir / "source-units").exists()
    assert not list(run_dir.glob(".layer2-source-stage-*"))
