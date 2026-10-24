import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path


SCHEMA_VERSION = 2
SOURCE_TYPES = {
    "short_video",
    "long_form",
    "article_or_interview",
    "research_report",
    "unknown",
}
INVALID_PATH_SEGMENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_DEVICE_NAME = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.IGNORECASE
)
OWNER_MARKER = ".layer2-owner.json"


def require_path_segment(name: str, value: object) -> str:
    segment = str(value)
    if (
        not segment.strip()
        or segment in {".", ".."}
        or segment.endswith((" ", "."))
        or INVALID_PATH_SEGMENT.search(segment)
        or WINDOWS_DEVICE_NAME.fullmatch(segment)
    ):
        raise ValueError(f"invalid {name}: {segment!r}")
    return segment


def normalize_raw_text(value: str) -> str:
    return value.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_identity(
    source_path: Path | str,
    source_sha256: str,
    locator: str,
    unit_sha256: str,
) -> str:
    normalized_path = str(Path(source_path).resolve()).casefold()
    payload = "\0".join((normalized_path, source_sha256, locator, unit_sha256))
    return sha256_text(payload)


def read_json(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path | str, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)


def write_jsonl(path: Path | str, rows: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def read_unit_text(source: Path, start_line: int | None, end_line: int | None) -> str:
    text = normalize_raw_text(source.read_text(encoding="utf-8-sig"))
    if start_line is None and end_line is None:
        selected = text
    else:
        lines = text.splitlines(keepends=True)
        start = 1 if start_line is None else int(start_line)
        end = len(lines) if end_line is None else int(end_line)
        if start < 1 or end < start or end > len(lines):
            raise ValueError(f"invalid line range {start}-{end} for {source}")
        selected = "".join(lines[start - 1:end])
    if not selected:
        raise ValueError(f"empty source unit: {source}")
    return selected


def validate_long_form_orders(units: list[dict]) -> None:
    seen: dict[str, set[int]] = {}
    for unit in units:
        if unit.get("source_type") != "long_form" or not unit.get("group_id"):
            continue
        order = unit.get("order")
        if isinstance(order, bool) or not isinstance(order, int) or order < 1:
            raise ValueError(
                f"grouped long_form requires positive integer order: "
                f"{unit.get('unit_id')}"
            )
        group_id = str(unit["group_id"])
        if order in seen.setdefault(group_id, set()):
            raise ValueError(
                f"duplicate long_form order in group {group_id}: {order}"
            )
        seen[group_id].add(order)


def prepare_inventory_records(spec: dict) -> list[tuple[dict, str]]:
    if spec.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported source-spec schema")
    units = spec.get("units")
    if not isinstance(units, list) or not units:
        raise ValueError("source-spec units must be a non-empty array")
    seen_units: set[str] = set()
    seen_cards: set[str] = set()
    prepared: list[tuple[dict, str]] = []
    for unit in units:
        unit_id = require_path_segment("unit_id", unit["unit_id"])
        card_id = require_path_segment("card_id", unit["card_id"])
        blogger = require_path_segment("blogger", unit["blogger"])
        normalized_unit_id = unit_id.casefold()
        normalized_card_id = card_id.casefold()
        if normalized_unit_id in seen_units:
            raise ValueError(f"duplicate unit_id: {unit_id}")
        if normalized_card_id in seen_cards:
            raise ValueError(f"duplicate card_id: {card_id}")
        seen_units.add(normalized_unit_id)
        seen_cards.add(normalized_card_id)
        source_type = str(unit.get("source_type") or "unknown")
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"unsupported source_type: {source_type}")
        primary_theme = str(unit["primary_theme"])
        if primary_theme not in {f"{value:02d}" for value in range(1, 9)}:
            raise ValueError(f"invalid primary_theme: {primary_theme}")
        source = Path(unit["path"])
        if not source.is_file():
            raise FileNotFoundError(source)
        locator = str(unit.get("locator") or "whole-file").strip()
        raw_text = read_unit_text(source, unit.get("start_line"), unit.get("end_line"))
        file_hash = sha256_file(source)
        unit_hash = sha256_text(raw_text)
        prepared.append(({
            "unit_id": unit_id,
            "card_id": card_id,
            "blogger": blogger,
            "source_title": str(unit["source_title"]).strip(),
            "source_path": str(source.resolve()),
            "source_sha256": file_hash,
            "source_locator": locator,
            "source_unit_sha256": unit_hash,
            "idempotency_key": make_identity(source, file_hash, locator, unit_hash),
            "source_type": source_type,
            "primary_theme": primary_theme,
            "group_id": unit.get("group_id"),
            "order": unit.get("order"),
            "start_line": (
                None if unit.get("start_line") is None else int(unit["start_line"])
            ),
            "end_line": (
                None if unit.get("end_line") is None else int(unit["end_line"])
            ),
        }, raw_text))
    validate_long_form_orders([record for record, _ in prepared])
    return prepared


def require_new_inventory_outputs(run: Path) -> None:
    for target in (run / "source-units", run / "source-inventory.json"):
        if target.exists():
            raise FileExistsError(f"immutable inventory output already exists: {target}")


def write_staged_inventory(
    prepared: list[tuple[dict, str]],
    stage: Path,
    create_context_map: bool,
    transaction_token: str,
) -> dict[str, str]:
    raw_dir = stage / "source-units"
    raw_dir.mkdir()
    write_json(raw_dir / OWNER_MARKER, {"transaction_token": transaction_token})
    records: list[dict] = []
    for record, raw_text in prepared:
        raw_output = raw_dir / f"{record['unit_id']}.txt"
        temporary = raw_output.with_suffix(".txt.tmp")
        temporary.write_text(raw_text, encoding="utf-8", newline="\n")
        os.replace(temporary, raw_output)
        record["raw_text_path"] = str(
            (stage.parent / "source-units" / raw_output.name).resolve()
        )
        records.append(record)
    inventory_path = stage / "source-inventory.json"
    write_json(inventory_path, {
        "schema_version": SCHEMA_VERSION,
        "count": len(records),
        "units": records,
    })
    hashes = {"source-inventory.json": sha256_file(inventory_path)}
    if create_context_map:
        context_map_path = stage / "context-map.json"
        write_json(context_map_path, {
            "schema_version": SCHEMA_VERSION,
            "groups": [],
        })
        hashes["context-map.json"] = sha256_file(context_map_path)
    return hashes


def directory_is_owned(path: Path, transaction_token: str) -> bool:
    try:
        marker = read_json(path / OWNER_MARKER)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return marker == {"transaction_token": transaction_token}


def file_is_owned(path: Path, expected_sha256: str) -> bool:
    try:
        return path.is_file() and sha256_file(path) == expected_sha256
    except OSError:
        return False


def rollback_published_outputs(
    published: list[tuple[str, Path, str]]
) -> None:
    for kind, target, proof in reversed(published):
        if kind == "directory" and directory_is_owned(target, proof):
            shutil.rmtree(target)
        elif kind == "file" and file_is_owned(target, proof):
            target.unlink()


def publish_staged_inventory(
    stage: Path,
    run: Path,
    create_context_map: bool,
    transaction_token: str,
    staged_file_hashes: dict[str, str],
) -> None:
    staged_outputs = [
        (stage / "source-units", run / "source-units"),
        (stage / "source-inventory.json", run / "source-inventory.json"),
    ]
    if create_context_map:
        staged_outputs.append((stage / "context-map.json", run / "context-map.json"))
    published: list[tuple[str, Path, str]] = []
    try:
        for staged, target in staged_outputs:
            if target.name == "context-map.json" and target.exists():
                continue
            if target.exists():
                raise FileExistsError(f"immutable inventory output already exists: {target}")
            os.rename(staged, target)
            if target.name == "source-units":
                published.append(("directory", target, transaction_token))
            else:
                published.append(("file", target, staged_file_hashes[target.name]))
        marker = run / "source-units" / OWNER_MARKER
        if directory_is_owned(run / "source-units", transaction_token):
            marker.unlink()
    except Exception:
        rollback_published_outputs(published)
        raise


def build_inventory(spec_path: Path | str, run_dir: Path | str) -> Path:
    run = Path(run_dir)
    require_new_inventory_outputs(run)
    prepared = prepare_inventory_records(read_json(spec_path))
    context_map_path = run / "context-map.json"
    create_context_map = not context_map_path.exists()
    run.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".layer2-source-stage-", dir=run))
    transaction_token = uuid.uuid4().hex
    try:
        staged_file_hashes = write_staged_inventory(
            prepared, stage, create_context_map, transaction_token
        )
        publish_staged_inventory(
            stage,
            run,
            create_context_map,
            transaction_token,
            staged_file_hashes,
        )
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return run / "source-inventory.json"


def prepare_context_packets(inventory: dict, context_map: dict) -> list[dict]:
    if inventory.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported source-inventory schema")
    inventory_units = inventory.get("units")
    if not isinstance(inventory_units, list):
        raise ValueError("source-inventory units must be an array")
    validate_long_form_orders(inventory_units)
    inventory_ids = [str(row["unit_id"]) for row in inventory_units]
    if len(inventory_ids) != len(set(inventory_ids)):
        raise ValueError("duplicate inventory unit_id")
    if context_map.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported context-map schema")
    group_rows = context_map.get("groups", [])
    if not isinstance(group_rows, list):
        raise ValueError("context-map groups must be an array")
    group_ids = [str(row["group_id"]) for row in group_rows]
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("duplicate context group_id")
    context_unit_ids: set[str] = set()
    for group in group_rows:
        references = [str(row["unit_id"]) for row in group.get("units", [])]
        duplicate_references = sorted(
            unit_id
            for unit_id in set(references)
            if references.count(unit_id) > 1
        )
        if duplicate_references:
            raise ValueError(f"duplicate context unit_id: {duplicate_references}")
        duplicate_across_groups = sorted(set(references) & context_unit_ids)
        if duplicate_across_groups:
            raise ValueError(f"duplicate context unit_id: {duplicate_across_groups}")
        context_unit_ids.update(references)
        unknown = sorted(set(references) - set(inventory_ids))
        if unknown:
            raise ValueError(f"context map references unknown units: {unknown}")
    groups = {row["group_id"]: row for row in group_rows}
    ordered = sorted(
        inventory_units,
        key=lambda row: (
            str(row.get("group_id") or ""),
            row.get("order") or 0,
            row["unit_id"],
        ),
    )
    group_units: dict[str, list[dict]] = {}
    for unit in ordered:
        if unit["source_type"] == "long_form" and unit.get("group_id"):
            group_units.setdefault(unit["group_id"], []).append(unit)
    complete_groups: dict[str, bool] = {}
    for group_id, group in groups.items():
        map_units = group.get("units", [])
        expected_unit_ids = [
            unit["unit_id"] for unit in group_units.get(group_id, [])
        ]
        map_unit_ids = [row["unit_id"] for row in map_units]
        complete_groups[group_id] = (
            "overview" in group
            and map_unit_ids == expected_unit_ids
            and all("summary" in row for row in map_units)
        )

    packets = []
    for unit in inventory_units:
        packet = {
            "unit_id": unit["unit_id"],
            "source_type": unit["source_type"],
            "mode": "whole_source",
            "context_status": "complete",
            "global_context": None,
            "previous": None,
            "current": {
                "unit_id": unit["unit_id"],
                "raw_text_path": unit["raw_text_path"],
            },
            "next": None,
        }
        if unit["source_type"] == "long_form":
            group = groups.get(unit.get("group_id"))
            members = group_units.get(unit.get("group_id") or "", [])
            position = next(
                (
                    index
                    for index, row in enumerate(members)
                    if row["unit_id"] == unit["unit_id"]
                ),
                -1,
            )
            packet["mode"] = "long_form_context"
            if not group or position < 0 or not complete_groups.get(unit.get("group_id")):
                packet["context_status"] = "partial"
            else:
                summaries = {
                    row["unit_id"]: row["summary"]
                    for row in group.get("units", [])
                }
                packet["global_context"] = {
                    "overview": group["overview"],
                    "glossary": group.get("glossary", []),
                }
                if position > 0:
                    previous = members[position - 1]
                    packet["previous"] = {
                        "unit_id": previous["unit_id"],
                        "summary": summaries.get(previous["unit_id"], ""),
                    }
                if position + 1 < len(members):
                    following = members[position + 1]
                    packet["next"] = {
                        "unit_id": following["unit_id"],
                        "summary": summaries.get(following["unit_id"], ""),
                        "raw_text_path": following["raw_text_path"],
                    }
        packets.append(packet)
    return packets


def build_context_packets(
    inventory_path: Path | str,
    context_map_path: Path | str | None,
    output_path: Path | str,
) -> Path:
    inventory = read_json(Path(inventory_path))
    context_map = (
        read_json(Path(context_map_path))
        if context_map_path
        else {"schema_version": SCHEMA_VERSION, "groups": []}
    )
    packets = prepare_context_packets(inventory, context_map)
    write_jsonl(Path(output_path), packets)
    return Path(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--spec", required=True, type=Path)
    inventory.add_argument("--run-dir", required=True, type=Path)
    context = commands.add_parser("context")
    context.add_argument("--inventory", required=True, type=Path)
    context.add_argument("--context-map", type=Path)
    context.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "inventory":
        result = build_inventory(args.spec, args.run_dir)
        payload = {"inventory": str(result.resolve())}
    else:
        result = build_context_packets(args.inventory, args.context_map, args.output)
        payload = {"context_packets": str(result.resolve())}
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
