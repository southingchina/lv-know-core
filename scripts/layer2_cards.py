import argparse
import html
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import layer2_source
import profile_config


EXTRACTION_VERSION = "lv-know-l2-v2"
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")


def render_raw_transcript(raw: str, source_type: str = "short_video") -> str:
    label = "完整原始正文" if source_type == "article_or_interview" else "完整原始口播稿"
    return "\n".join([
        "<details>",
        f"<summary>{label}</summary>",
        "",
        f'<pre data-lv-know="raw-transcript">{html.escape(raw, quote=False)}</pre>',
        "",
        "</details>",
    ])


def load_extractions(path: Path | None) -> dict[str, dict]:
    if path is None or not path.exists():
        return {}
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        unit_id = str(row["unit_id"])
        if unit_id in rows:
            raise ValueError(f"duplicate extraction unit_id: {unit_id}")
        rows[unit_id] = row
    return rows


def render_optional_sections(extraction: dict, raw: str) -> list[str]:
    lines: list[str] = []
    hook = extraction.get("hook")
    if hook is not None:
        if not isinstance(hook, dict):
            raise ValueError("hook must be an object")
        quote = str(hook.get("quote") or "").strip()
        function = str(hook.get("function") or "").strip()
        if not quote or not function:
            raise ValueError("blank hook quote or function")
        if not raw.lstrip().startswith(quote):
            raise ValueError("hook is not an opening prefix")
        lines.extend([
            "## 表达结构",
            "",
            "### 开场钩子",
            "",
            f"> {quote}",
            "",
            function,
            "",
        ])
    for section in extraction.get("sections") or []:
        heading = str(section.get("heading") or "").strip()
        if not heading:
            raise ValueError("blank section heading")
        paragraphs = [
            str(value).strip()
            for value in section.get("paragraphs") or []
            if str(value).strip()
        ]
        items = section.get("items") or []
        clean_items: list[str] = []
        for item in items:
            if isinstance(item, dict):
                label = str(item.get("label") or "").strip()
                text = str(item.get("text") or "").strip()
                if not label or not text:
                    raise ValueError("blank item label or text")
                clean_items.append(f"- **{label}：** {text}")
            else:
                clean = str(item).strip()
                if not clean:
                    raise ValueError("blank list item")
                clean_items.append(f"- {clean}")
        if not paragraphs and not items:
            continue
        lines.extend([f"## {heading}", ""])
        for paragraph in paragraphs:
            lines.extend([paragraph, ""])
        lines.extend(clean_items)
        if clean_items:
            lines.append("")
    return lines


def render_card(
    unit: dict,
    extraction: dict | None,
    profile_spec: dict | None = None,
) -> tuple[str, str, str]:
    profile_spec = profile_spec or profile_config.load_profile("geo")
    raw = Path(unit["raw_text_path"]).read_text(encoding="utf-8")
    result = extraction or {
        "unit_id": unit["unit_id"],
        "card_title": unit["source_title"],
        "additional_tags": [],
        "enrichment_status": "partial",
        "sections": [],
        "calibrated_transcript": None,
    }
    title = str(result.get("card_title") or unit["source_title"]).strip()
    status = str(result.get("enrichment_status") or "partial")
    if status not in {"complete", "partial"}:
        raise ValueError(f"invalid enrichment_status: {status}")
    theme_tag = profile_config.theme_tag(profile_spec, str(unit["primary_theme"]))
    taxonomy = profile_spec["taxonomy"]
    tags = [
        str(tag).format(blogger=unit["blogger"], primary_theme=unit["primary_theme"])
        for tag in taxonomy["base_tags"]
    ]
    tags.append(theme_tag)
    allowed_additional_tags = set(taxonomy["allowed_additional_tags"]) | {theme_tag}
    for tag in result.get("additional_tags") or []:
        clean = str(tag).strip()
        if clean not in allowed_additional_tags:
            raise ValueError(f"unsupported additional tag: {clean}")
        if clean not in tags:
            tags.append(clean)
    frontmatter = [
        "---",
        f"id: {unit['card_id']}",
        "note_type: source_master",
        "layer: 2",
        "eligible_for_l3: true",
        f"source_unit_id: {json.dumps(unit['unit_id'], ensure_ascii=False)}",
        f"blogger: {json.dumps(unit['blogger'], ensure_ascii=False)}",
        f"source_type: {unit['source_type']}",
        f"source_locator: {json.dumps(unit['source_locator'], ensure_ascii=False)}",
        f"source_unit_sha256: {json.dumps(unit['source_unit_sha256'])}",
        f"idempotency_key: {json.dumps(unit['idempotency_key'])}",
        f'extraction_version: "{EXTRACTION_VERSION}"',
        f"enrichment_status: {status}",
        f'primary_theme: "{unit["primary_theme"]}"',
        "tags:",
        *[f"  - {tag}" for tag in tags],
        f"source_title: {json.dumps(unit['source_title'], ensure_ascii=False)}",
        f"source_path: {json.dumps(unit['source_path'], ensure_ascii=False)}",
        f"source_sha256: {json.dumps(unit['source_sha256'])}",
        "evidence_status: source_viewpoint",
        "---",
        "",
        f"# {title}",
        "",
    ]
    body = render_optional_sections(result, raw)
    calibrated = result.get("calibrated_transcript")
    if calibrated:
        body.extend([
            "<details>",
            "<summary>校对版正文</summary>" if unit["source_type"] == "article_or_interview" else "<summary>校对版口播稿</summary>",
            "",
            f'<pre data-lv-know="calibrated-transcript">{html.escape(str(calibrated), quote=False)}</pre>',
            "",
            "</details>",
            "",
        ])
    body.extend([render_raw_transcript(raw, unit["source_type"]), ""])
    return "\n".join(frontmatter + body), title, status


def safe_filename(title: str, fallback: str = "素材") -> str:
    return re.sub(r'[<>:"/\\|?*]', "", title).strip(" .")[:54] or fallback


def existing_row_identity(row: dict) -> str | None:
    explicit = str(row.get("idempotency_key") or "").strip()
    if SHA256_PATTERN.fullmatch(explicit):
        return explicit
    fields = (
        row.get("source_path"),
        row.get("source_sha256"),
        row.get("source_locator"),
        row.get("source_unit_sha256"),
    )
    if not all(value is not None and str(value).strip() for value in fields):
        return None
    return layer2_source.make_identity(
        str(fields[0]),
        str(fields[1]),
        str(fields[2]),
        str(fields[3]),
    )


def load_existing_manifest(path: Path | None) -> list[dict]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("existing manifest must be a JSON array")
    rows: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("existing manifest rows must be objects")
        rows.append(row)
    return rows


def map_existing_units(
    units: list[dict],
    existing_rows: list[dict],
) -> tuple[list[dict], set[str]]:
    identities: dict[str, dict] = {}
    ids: dict[str, dict] = {}
    for row in existing_rows:
        card_id = str(row.get("id") or "").strip()
        if card_id:
            card_key = card_id.casefold()
            if card_key in ids:
                raise ValueError(f"duplicate existing card_id: {card_id}")
            ids[card_key] = row
        identity = existing_row_identity(row)
        if identity:
            if identity in identities:
                raise ValueError(f"duplicate existing identity: {identity}")
            identities[identity] = row

    mapped: list[dict] = []
    mapped_unit_ids: set[str] = set()
    for unit in units:
        identity = str(unit["idempotency_key"])
        existing = identities.get(identity)
        if existing is not None:
            card_id = str(existing.get("id") or "").strip()
            relative_path = str(existing.get("relative_path") or "").strip()
            if not card_id or not relative_path:
                raise ValueError(
                    f"existing identity missing id or relative_path: {identity}"
                )
            mapped.append({
                "unit_id": unit["unit_id"],
                "id": card_id,
                "relative_path": relative_path,
                "source_path": unit["source_path"],
                "source_sha256": unit["source_sha256"],
                "source_locator": unit["source_locator"],
                "source_unit_sha256": unit["source_unit_sha256"],
                "idempotency_key": identity,
            })
            mapped_unit_ids.add(str(unit["unit_id"]))
            continue
        card_id = str(unit["card_id"])
        if card_id.casefold() in ids:
            raise ValueError(
                f"existing card_id collision for different identity: {card_id}"
            )
    return mapped, mapped_unit_ids


def build_render_plan(
    inventory: dict,
    extractions: dict[str, dict],
    skip_unit_ids: set[str] | None = None,
    profile: str = "geo",
    profile_config_path: Path | str | None = None,
) -> list[dict]:
    profile_spec = profile_config.load_profile(profile, profile_config_path)
    if inventory.get("schema_version") != 2:
        raise ValueError("unsupported source-inventory schema")
    units = inventory.get("units")
    if not isinstance(units, list):
        raise ValueError("source-inventory units must be an array")
    known_units = {str(unit["unit_id"]) for unit in units}
    unknown = sorted(set(extractions) - known_units)
    if unknown:
        raise ValueError(f"extraction references unknown units: {unknown}")
    planned: list[dict] = []
    used_card_ids: set[str] = set()
    used_paths: set[str] = set()
    for unit in units:
        unit_id = str(unit["unit_id"])
        if unit_id in (skip_unit_ids or set()):
            continue
        blogger = layer2_source.require_path_segment("blogger", unit["blogger"])
        card_id = layer2_source.require_path_segment("card_id", unit["card_id"])
        card_key = card_id.casefold()
        if card_key in used_card_ids:
            raise ValueError(f"duplicate card_id: {card_id}")
        used_card_ids.add(card_key)
        card, title, status = render_card(unit, extractions.get(unit_id), profile_spec)
        relative = (
            Path(profile_config.route_for_source(profile_spec, str(unit["source_type"])))
            / blogger
            / f"{card_id}_{safe_filename(title, profile_spec['taxonomy']['filename_fallback'])}.md"
        )
        path_key = relative.as_posix().casefold()
        if path_key in used_paths:
            raise ValueError(f"duplicate output path: {relative}")
        used_paths.add(path_key)
        planned.append({
            "unit": unit,
            "card": card,
            "blogger": blogger,
            "card_id": card_id,
            "relative_path": relative,
            "enrichment_status": status,
        })
    return planned


def write_card(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def render_stage(
    inventory_path: Path | str,
    extraction_path: Path | str | None,
    run_dir: Path | str,
    existing_manifest_path: Path | str | None = None,
    profile: str = "geo",
    profile_config_path: Path | str | None = None,
) -> Path:
    run = Path(run_dir)
    inventory = layer2_source.read_json(Path(inventory_path))
    units = inventory.get("units")
    if isinstance(units, list) and any(
        unit.get("source_type") == "research_report" for unit in units
    ):
        raise ValueError(
            "research_report is not supported by the generic transcript renderer; "
            "use references/research-report-contract.md and "
            "assets/research-report-card-template.md"
        )
    extractions = load_extractions(Path(extraction_path) if extraction_path else None)
    if not isinstance(units, list):
        raise ValueError("source-inventory units must be an array")
    existing_rows = load_existing_manifest(
        Path(existing_manifest_path) if existing_manifest_path else None
    )
    existing_units, mapped_unit_ids = map_existing_units(units, existing_rows)
    planned = build_render_plan(
        inventory,
        extractions,
        mapped_unit_ids,
        profile,
        profile_config_path,
    )
    cards_root = run / "layer2-cards"
    manifest_path = run / "staged-layer2-manifest.json"
    if cards_root.exists() or manifest_path.exists():
        raise FileExistsError("staged renderer output already exists")
    run.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".layer2-cards-stage-", dir=run))
    try:
        staged_cards = stage / "layer2-cards"
        staged_cards.mkdir()
        manifest_cards: list[dict] = []
        for row in planned:
            unit = row["unit"]
            relative = row["relative_path"]
            output = staged_cards / relative
            write_card(output, row["card"])
            manifest_cards.append({
                "unit_id": unit["unit_id"],
                "id": row["card_id"],
                "relative_path": relative.as_posix(),
                "output_path": str((cards_root / relative).resolve()),
                "source_path": unit["source_path"],
                "source_sha256": unit["source_sha256"],
                "source_locator": unit["source_locator"],
                "source_unit_sha256": unit["source_unit_sha256"],
                "source_unit_id": unit["unit_id"],
                "idempotency_key": unit["idempotency_key"],
                "note_type": "source_master",
                "eligible_for_l3": True,
                "source_type": unit["source_type"],
                "enrichment_status": row["enrichment_status"],
            })
        staged_manifest = stage / "staged-layer2-manifest.json"
        layer2_source.write_json(staged_manifest, {
            "schema_version": 2,
            "cards": manifest_cards,
            "existing_units": existing_units,
            "failures": [],
        })
        os.replace(staged_cards, cards_root)
        os.replace(staged_manifest, manifest_path)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--extraction", type=Path)
    parser.add_argument("--existing-manifest", type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--profile", default="geo")
    parser.add_argument("--profile-config", type=Path)
    args = parser.parse_args()
    path = render_stage(
        args.inventory,
        args.extraction,
        args.run_dir,
        args.existing_manifest,
        args.profile,
        args.profile_config,
    )
    print(json.dumps({"manifest": str(path.resolve())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
