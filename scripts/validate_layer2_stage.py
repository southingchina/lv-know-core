import argparse
import html
import json
import re
from collections import Counter
from pathlib import Path

import layer2_source


class Layer2ValidationError(RuntimeError):
    pass


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_frontmatter(card_text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", card_text, flags=re.S)
    if not match:
        raise Layer2ValidationError("frontmatter missing")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        scalar = re.match(r"^([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if not scalar or not scalar.group(2):
            continue
        value = scalar.group(2)
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as error:
                raise Layer2ValidationError(
                    f"invalid quoted frontmatter: {error.msg}"
                ) from error
        fields[scalar.group(1)] = str(value)
    return fields


def extract_raw_transcript(card_text: str) -> str:
    match = re.search(
        r'<pre data-lv-know="raw-transcript">(.*?)</pre>',
        card_text,
        flags=re.S,
    )
    if not match:
        raise Layer2ValidationError("complete raw transcript block missing")
    return html.unescape(match.group(1))


def write_failure_report(run: Path, error: str) -> None:
    layer2_source.write_json(
        run / "reports" / "layer2-validation.json",
        {"status": "fail", "error": error},
    )


def require_unique(values: list[str], label: str) -> None:
    duplicates = sorted(value for value, count in Counter(values).items() if count > 1)
    if duplicates:
        raise Layer2ValidationError(f"duplicate {label}: {duplicates}")


def hook_quote(card_text: str) -> str | None:
    match = re.search(r"^### 开场钩子\n\n> (.+)$", card_text, flags=re.M)
    return match.group(1).strip() if match else None


def validate_inventory_source(unit: dict) -> None:
    unit_id = unit["unit_id"]
    source = Path(unit["source_path"])
    if not source.is_file() or layer2_source.sha256_file(source) != unit["source_sha256"]:
        raise Layer2ValidationError(f"source file hash mismatch: {unit_id}")
    original_unit = layer2_source.read_unit_text(
        source,
        unit.get("start_line"),
        unit.get("end_line"),
    )
    if layer2_source.sha256_text(original_unit) != unit["source_unit_sha256"]:
        raise Layer2ValidationError(f"source unit hash mismatch: {unit_id}")
    raw_copy = layer2_source.normalize_raw_text(
        Path(unit["raw_text_path"]).read_text(encoding="utf-8-sig")
    )
    if raw_copy.encode("utf-8") != original_unit.encode("utf-8"):
        raise Layer2ValidationError(f"raw source unit mismatch: {unit_id}")
    expected_identity = layer2_source.make_identity(
        source,
        unit["source_sha256"],
        unit["source_locator"],
        unit["source_unit_sha256"],
    )
    if unit.get("idempotency_key") != expected_identity:
        raise Layer2ValidationError(f"idempotency key mismatch: {unit_id}")


def validate_long_form_context(
    unit: dict,
    packet: dict | None,
    expected: dict,
) -> str | None:
    return None if packet == expected else unit["unit_id"]


def validate_layer2_stage(run_dir: Path | str) -> dict:
    run = Path(run_dir)
    report_written = False
    try:
        inventory = layer2_source.read_json(run / "source-inventory.json")
        manifest = layer2_source.read_json(run / "staged-layer2-manifest.json")
        packet_rows = read_jsonl(run / "context-packets.jsonl")
        require_unique([str(row["unit_id"]) for row in packet_rows], "context packet")
        packets = {row["unit_id"]: row for row in packet_rows}
        units = {row["unit_id"]: row for row in inventory["units"]}
        cards = manifest.get("cards", [])
        existing = manifest.get("existing_units", [])
        failures = manifest.get("failures", [])

        for unit in units.values():
            validate_inventory_source(unit)
        context_map_path = run / "context-map.json"
        context_map = (
            layer2_source.read_json(context_map_path)
            if context_map_path.is_file()
            else {"schema_version": 2, "groups": []}
        )
        expected_packets = {
            row["unit_id"]: row
            for row in layer2_source.prepare_context_packets(inventory, context_map)
        }

        accounting_rows = cards + existing + failures
        accounting_ids = [row["unit_id"] for row in accounting_rows]
        require_unique(accounting_ids, "accounting unit")
        accounted = set(accounting_ids)
        expected = set(units)
        missing = sorted(expected - accounted)
        if missing:
            raise Layer2ValidationError(f"unaccounted input unit: {missing}")
        unexpected = sorted(accounted - expected)
        if unexpected:
            raise Layer2ValidationError(f"unknown accounted unit: {unexpected}")

        require_unique([str(row["id"]).casefold() for row in cards], "staged card id")
        require_unique(
            [str(row["id"]).casefold() for row in cards + existing],
            "accounted card id",
        )
        require_unique(
            [str(row["output_path"]).casefold() for row in cards],
            "staged output path",
        )

        raw_mismatches: list[str] = []
        context_errors: list[str] = []
        for row in existing:
            unit = units[row["unit_id"]]
            expected_mapping = {
                "source_path": unit["source_path"],
                "source_sha256": unit["source_sha256"],
                "source_locator": unit["source_locator"],
                "source_unit_sha256": unit["source_unit_sha256"],
                "idempotency_key": unit["idempotency_key"],
            }
            if (
                not str(row.get("id") or "").strip()
                or not str(row.get("relative_path") or "").strip()
                or any(
                    str(row.get(key)) != str(value)
                    for key, value in expected_mapping.items()
                )
            ):
                raise Layer2ValidationError(
                    f"existing unit identity mismatch: {unit['unit_id']}"
                )
        short_counts = Counter(
            row["unit_id"]
            for row in cards
            if units[row["unit_id"]]["source_type"] == "short_video"
        )
        short_errors = [
            unit_id
            for unit_id, unit in units.items()
            if unit["source_type"] == "short_video"
            and unit_id in {row["unit_id"] for row in cards}
            and short_counts[unit_id] != 1
        ]
        staged_root = (run / "layer2-cards").resolve()
        for row in cards:
            unit = units[row["unit_id"]]
            output = Path(row["output_path"]).resolve()
            if not output.is_relative_to(staged_root):
                raise Layer2ValidationError(f"card path escapes staging root: {output}")
            if not output.is_file():
                raise Layer2ValidationError(f"staged card missing: {output}")

            card_text = output.read_text(encoding="utf-8")
            fields = parse_frontmatter(card_text)
            expected_manifest_fields = {
                "id": unit["card_id"],
                "source_unit_id": unit["unit_id"],
                "source_path": unit["source_path"],
                "source_sha256": unit["source_sha256"],
                "source_locator": unit["source_locator"],
                "source_unit_sha256": unit["source_unit_sha256"],
                "idempotency_key": unit["idempotency_key"],
            }
            for key, expected_value in expected_manifest_fields.items():
                if str(row.get(key)) != str(expected_value):
                    raise Layer2ValidationError(
                        f"staged manifest mismatch {unit['unit_id']} {key}: "
                        f"{row.get(key)!r} != {expected_value!r}"
                    )
            expected_fields = {
                "id": unit["card_id"],
                "note_type": "source_master",
                "layer": "2",
                "eligible_for_l3": "true",
                "source_unit_id": unit["unit_id"],
                "source_type": unit["source_type"],
                "source_locator": unit["source_locator"],
                "source_unit_sha256": unit["source_unit_sha256"],
                "idempotency_key": unit["idempotency_key"],
                "extraction_version": "lv-know-l2-v2",
                "enrichment_status": row["enrichment_status"],
                "source_sha256": unit["source_sha256"],
                "primary_theme": unit["primary_theme"],
            }
            for key, expected_value in expected_fields.items():
                if fields.get(key) != str(expected_value):
                    raise Layer2ValidationError(
                        f"frontmatter mismatch {unit['unit_id']} {key}: "
                        f"{fields.get(key)!r} != {expected_value!r}"
                    )
            if row["enrichment_status"] not in {"complete", "partial"}:
                raise Layer2ValidationError(f"invalid enrichment status: {unit['unit_id']}")
            if not re.fullmatch(r"[a-f0-9]{64}", unit["source_unit_sha256"]):
                raise Layer2ValidationError(f"invalid source unit hash: {unit['unit_id']}")

            expected_raw = Path(unit["raw_text_path"]).read_text(encoding="utf-8")
            if extract_raw_transcript(card_text) != expected_raw:
                raw_mismatches.append(unit["unit_id"])
            quote = hook_quote(card_text)
            if quote and not expected_raw.lstrip().startswith(quote):
                raise Layer2ValidationError(
                    f"hook is not opening raw prefix: {unit['unit_id']}"
                )
            if unit["source_type"] == "long_form":
                context_error = validate_long_form_context(
                    unit,
                    packets.get(unit["unit_id"]),
                    expected_packets[unit["unit_id"]],
                )
                if context_error:
                    context_errors.append(context_error)

        result = {
            "status": "pass",
            "counts": {
                "input_units": len(units),
                "staged_cards": len(cards),
                "existing_units": len(existing),
                "failures": len(failures),
                "partial_enrichment": sum(
                    row.get("enrichment_status") == "partial" for row in cards
                ),
            },
            "short_video_split_errors": short_errors,
            "raw_transcript_mismatches": raw_mismatches,
            "long_form_context_errors": context_errors,
        }
        if short_errors or raw_mismatches or context_errors:
            result["status"] = "fail"
            if context_errors:
                result["error"] = f"long-form context invalid: {context_errors}"
            elif raw_mismatches:
                result["error"] = f"raw transcript mismatch: {raw_mismatches}"
            else:
                result["error"] = f"short-video split invalid: {short_errors}"
        layer2_source.write_json(run / "reports" / "layer2-validation.json", result)
        report_written = True
        if result["status"] != "pass":
            raise Layer2ValidationError(json.dumps(result, ensure_ascii=False))
        return result
    except Layer2ValidationError as error:
        if not report_written:
            write_failure_report(run, str(error))
        raise
    except Exception as error:
        validation_error = Layer2ValidationError(f"validation input error: {error}")
        if not report_written:
            write_failure_report(run, str(validation_error))
        raise validation_error from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = validate_layer2_stage(args.run_dir)
    except Layer2ValidationError as error:
        print(json.dumps({"status": "fail", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
