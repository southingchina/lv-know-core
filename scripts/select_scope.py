#!/usr/bin/env python3
"""Select Layer 2 cards for a Layer 3 run without modifying the vault."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


REQUIRED_FIELDS = (
    "id",
    "title",
    "blogger",
    "primary_theme",
    "relative_path",
    "note_type",
    "source_type",
    "source_unit_id",
    "source_unit_sha256",
    "file_sha256",
    "card_content_sha256",
    "enrichment_sha256",
    "source_context_hash",
    "semantic_input_sha256",
)
SUPPORTED_MODES = ("all", "incremental", "theme", "blogger", "ids")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _manifest_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if payload.get("schema_version") != 2:
            raise ValueError("Manifest object must declare schema_version 2")
        rows = payload.get("cards")
    else:
        rows = None
    if not isinstance(rows, list):
        raise ValueError("Manifest must be a JSON array or schema_version 2 object with a cards array")
    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Manifest row {position} is not an object")
        missing = [field for field in REQUIRED_FIELDS if not str(row.get(field, "")).strip()]
        if missing:
            raise ValueError(f"Manifest row {position} is missing fields: {missing}")
    return rows


def validate_unique_ids(rows: Iterable[dict[str, Any]]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        card_id = str(row["id"])
        if card_id in seen:
            duplicates.add(card_id)
        seen.add(card_id)
    if duplicates:
        raise ValueError(f"Duplicate card ids: {sorted(duplicates)}")


def _previous_hashes(payload: Any) -> dict[str, str]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("Run state must be an object")
    cards = payload.get("cards", {})
    if not isinstance(cards, dict):
        raise ValueError("Run state cards must be an object")
    hashes: dict[str, str] = {}
    for card_id, value in cards.items():
        if not isinstance(value, dict):
            raise ValueError(f"Run state card {card_id!r} must be an object")
        semantic_hash = str(value.get("semantic_input_sha256", ""))
        if not SHA256_PATTERN.fullmatch(semantic_hash):
            raise ValueError(
                f"Run state card {card_id!r} has invalid semantic_input_sha256; "
                "expected 64 lowercase hexadecimal characters"
            )
        hashes[str(card_id)] = semantic_hash
    return hashes


def _with_reason(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**row, "selection_reason": reason}


def select_scope(
    manifest_rows: Any,
    previous_cards: dict[str, Any] | None,
    mode: str = "incremental",
    ids: Iterable[str] = (),
    themes: Iterable[str] = (),
    bloggers: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return deterministically ordered cards selected for the requested mode."""
    rows = _manifest_rows(manifest_rows)
    validate_unique_ids(rows)
    ordered = sorted(rows, key=lambda row: str(row["id"]))
    previous = _previous_hashes(previous_cards)
    id_set = {str(value) for value in ids}
    theme_set = {str(value) for value in themes}
    blogger_set = {str(value) for value in bloggers}

    if mode == "all":
        return [_with_reason(row, "all") for row in ordered]
    if mode == "incremental":
        selected: list[dict[str, Any]] = []
        for row in ordered:
            card_id = str(row["id"])
            if card_id not in previous:
                selected.append(_with_reason(row, "new"))
            elif previous[card_id] != str(row["semantic_input_sha256"]):
                selected.append(_with_reason(row, "changed"))
        return selected
    if mode == "theme":
        if not theme_set:
            raise ValueError("theme mode requires at least one --theme")
        return [_with_reason(row, "theme") for row in ordered if str(row["primary_theme"]) in theme_set]
    if mode == "blogger":
        if not blogger_set:
            raise ValueError("blogger mode requires at least one --blogger")
        return [_with_reason(row, "blogger") for row in ordered if str(row["blogger"]) in blogger_set]
    if mode == "ids":
        if not id_set:
            raise ValueError("ids mode requires at least one --id")
        manifest_ids = {str(row["id"]) for row in ordered}
        missing = sorted(id_set - manifest_ids)
        if missing:
            raise ValueError(f"Unknown card ids: {missing}")
        return [_with_reason(row, "ids") for row in ordered if str(row["id"]) in id_set]
    raise ValueError(f"Unsupported mode: {mode}; expected one of {SUPPORTED_MODES}")


def _read_json(path: Path | None) -> Any:
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--mode", choices=SUPPORTED_MODES, default="incremental")
    parser.add_argument("--id", action="append", default=[])
    parser.add_argument("--theme", action="append", default=[])
    parser.add_argument("--blogger", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    selected = select_scope(
        _read_json(args.manifest),
        _read_json(args.state),
        mode=args.mode,
        ids=args.id,
        themes=args.theme,
        bloggers=args.blogger,
    )
    payload = {
        "schema_version": 2,
        "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        "mode": args.mode,
        "count": len(selected),
        "cards": selected,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "mode": args.mode, "count": len(selected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
