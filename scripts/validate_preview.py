#!/usr/bin/env python3
"""Validate a staged Layer 3 preview without modifying any knowledge base."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


class PreviewValidationError(ValueError):
    """Raised when a preview violates the Layer 3 data contract."""


METRIC_INPUT_FILES = (
    "scope.json",
    "candidates.jsonl",
    "atoms.jsonl",
    "atom-index.jsonl",
    "question-registry.json",
    "source-map.json",
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PreviewValidationError(f"missing file: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise PreviewValidationError(f"invalid JSON in {path.name}:{line_number}: {error.msg}") from error
        if not isinstance(row, dict):
            raise PreviewValidationError(f"{path.name}:{line_number} must be an object")
        rows.append(row)
    return rows


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PreviewValidationError(f"missing file: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise PreviewValidationError(f"invalid JSON in {path.name}: {error.msg}") from error
    if not isinstance(payload, dict):
        raise PreviewValidationError(f"{path.name} must contain an object")
    return payload


def require_unique(rows: Iterable[dict[str, Any]], key: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in rows:
        value = str(row.get(key, "")).strip()
        if not value:
            raise PreviewValidationError(f"missing {key}")
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise PreviewValidationError(f"duplicate {key}: {sorted(duplicates)}")


def require_fields(row: dict[str, Any], fields: Iterable[str], label: str) -> None:
    missing = [field for field in fields if row.get(field) in (None, "", [])]
    if missing:
        raise PreviewValidationError(f"{label} missing fields: {missing}")


def validate_candidate_dispositions(candidates: list[dict[str, Any]]) -> None:
    allowed = {"进入三层", "待人工判断", "仅保留二层"}
    required = (
        "candidate_id",
        "answerable_question",
        "claim",
        "source_card_id",
        "source_blogger",
        "source_card_sha256",
        "candidate_hash",
        "disposition",
    )
    for row in candidates:
        label = f"candidate {row.get('candidate_id', '<missing>')}"
        require_fields(row, required, label)
        if row["disposition"] not in allowed:
            raise PreviewValidationError(f"{label} has invalid disposition: {row['disposition']}")
        if row["disposition"] == "进入三层" and not row.get("question_keys"):
            raise PreviewValidationError(f"{label} entering Layer 3 requires question_keys")
        if row["disposition"] in {"待人工判断", "仅保留二层"} and not str(
            row.get("disposition_reason", "")
        ).strip():
            raise PreviewValidationError(f"{label} requires disposition_reason")


def validate_scope_sources(scope: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    cards = scope.get("cards")
    if not isinstance(cards, list):
        raise PreviewValidationError("scope.json requires a cards array")
    if scope.get("count") != len(cards):
        raise PreviewValidationError("scope.json count does not match cards")
    card_by_id: dict[str, dict[str, Any]] = {}
    for position, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise PreviewValidationError(f"scope card {position} must be an object")
        require_fields(card, ("id", "sha256", "relative_path"), f"scope card {position}")
        card_id = str(card["id"])
        if card_id in card_by_id:
            raise PreviewValidationError(f"duplicate scope card id: {card_id}")
        card_by_id[card_id] = card

    for candidate in candidates:
        source_id = str(candidate["source_card_id"])
        scoped_card = card_by_id.get(source_id)
        if scoped_card is None:
            continue
        if str(candidate["source_card_sha256"]).lower() != str(scoped_card["sha256"]).lower():
            raise PreviewValidationError(
                f"candidate {candidate['candidate_id']} scope source hash does not match {source_id}"
            )


def registered_question_keys(registry: dict[str, Any]) -> set[str]:
    questions = registry.get("questions")
    if isinstance(questions, dict):
        return {str(key) for key in questions}
    if not isinstance(questions, list):
        raise PreviewValidationError("question-registry.json requires a questions array or object")
    keys: set[str] = set()
    for position, question in enumerate(questions, start=1):
        if not isinstance(question, dict) or not str(question.get("question_key", "")).strip():
            raise PreviewValidationError(f"question registry row {position} requires question_key")
        question_key = str(question["question_key"])
        if question_key in keys:
            raise PreviewValidationError(f"duplicate question key: {question_key}")
        keys.add(question_key)
    return keys


def validate_question_registry(atoms: list[dict[str, Any]], registry: dict[str, Any]) -> None:
    known = registered_question_keys(registry)
    for atom in atoms:
        unknown = sorted({str(value) for value in atom.get("question_keys", [])} - known)
        if unknown:
            raise PreviewValidationError(
                f"atom {atom['atom_id']} has unregistered question key: {unknown}"
            )


def validate_atom_index(index_rows: list[dict[str, Any]], atoms: list[dict[str, Any]]) -> None:
    require_unique(index_rows, "atom_id")
    indexed = {str(row["atom_id"]): row for row in index_rows}
    expected_ids = {str(atom["atom_id"]) for atom in atoms}
    if set(indexed) != expected_ids:
        raise PreviewValidationError("atom-index ids do not match atoms.jsonl")

    for atom in atoms:
        atom_id = str(atom["atom_id"])
        row = indexed[atom_id]
        comparisons = {
            "question_keys": sorted(str(value) for value in atom.get("question_keys", [])),
            "primary_theme": str(atom.get("primary_theme", "")),
            "related_themes": sorted(str(value) for value in atom.get("related_themes", [])),
            "knowledge_status": str(atom.get("knowledge_status", "")),
            "content_hash": str(atom.get("content_hash", "")),
        }
        actual = {
            "question_keys": sorted(str(value) for value in row.get("question_keys", [])),
            "primary_theme": str(row.get("primary_theme", "")),
            "related_themes": sorted(str(value) for value in row.get("related_themes", [])),
            "knowledge_status": str(row.get("knowledge_status", "")),
            "content_hash": str(row.get("content_hash", "")),
        }
        if actual != comparisons:
            raise PreviewValidationError(f"atom-index row does not match atom {atom_id}")
        if "source_count" in row and int(row["source_count"]) != len(set(atom["source_card_ids"])):
            raise PreviewValidationError(f"atom-index source_count does not match atom {atom_id}")


def validate_atom_sources(
    atoms: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    candidate_by_id = {str(row["candidate_id"]): row for row in candidates}
    atom_by_id = {str(row["atom_id"]): row for row in atoms}
    used_candidates: set[str] = set()
    required = (
        "atom_id",
        "answerable_question",
        "question_keys",
        "claim",
        "knowledge_status",
        "boundary",
        "source_card_ids",
        "source_candidate_ids",
        "version",
        "content_hash",
    )
    for atom in atoms:
        atom_id = str(atom.get("atom_id", "<missing>"))
        require_fields(atom, required, f"atom {atom_id}")
        source_candidates = [str(value) for value in atom["source_candidate_ids"]]
        unknown = sorted(set(source_candidates) - candidate_by_id.keys())
        if unknown:
            raise PreviewValidationError(f"atom {atom_id} references unknown source candidate: {unknown}")
        source_rows = [candidate_by_id[candidate_id] for candidate_id in source_candidates]
        if any(row["disposition"] != "进入三层" for row in source_rows):
            raise PreviewValidationError(f"atom {atom_id} references a Layer-2-only candidate")
        candidate_cards = {str(row["source_card_id"]) for row in source_rows}
        atom_cards = {str(value) for value in atom["source_card_ids"]}
        if candidate_cards != atom_cards:
            raise PreviewValidationError(f"atom {atom_id} source cards do not match source candidates")
        used_candidates.update(source_candidates)

        if atom["knowledge_status"] == "多人共识":
            bloggers = {str(row["source_blogger"]) for row in source_rows}
            if len(bloggers) < 2:
                raise PreviewValidationError(f"atom {atom_id} consensus requires at least two distinct bloggers")
        if atom["knowledge_status"] == "争议总览" and len(set(atom.get("related_atom_ids", []))) < 2:
            raise PreviewValidationError(f"atom {atom_id} conflict overview requires at least two related atoms")

    entering = {str(row["candidate_id"]) for row in candidates if row["disposition"] == "进入三层"}
    missing_atoms = sorted(entering - used_candidates)
    if missing_atoms:
        raise PreviewValidationError(f"Layer 3 candidates are not mapped to atoms: {missing_atoms}")
    return atom_by_id, candidate_by_id


def validate_bidirectional_map(
    source_map: dict[str, Any], atoms: list[dict[str, Any]]
) -> None:
    card_to_atoms = source_map.get("card_to_atoms")
    atom_to_cards = source_map.get("atom_to_cards")
    if not isinstance(card_to_atoms, dict) or not isinstance(atom_to_cards, dict):
        raise PreviewValidationError("source-map.json requires card_to_atoms and atom_to_cards objects")

    expected_atom_to_cards = {
        str(atom["atom_id"]): sorted({str(card_id) for card_id in atom["source_card_ids"]})
        for atom in atoms
    }
    expected_card_to_atoms: dict[str, list[str]] = defaultdict(list)
    for atom_id, card_ids in expected_atom_to_cards.items():
        for card_id in card_ids:
            expected_card_to_atoms[card_id].append(atom_id)
    expected_card_to_atoms = {
        card_id: sorted(atom_ids) for card_id, atom_ids in sorted(expected_card_to_atoms.items())
    }

    normalized_atom_to_cards = {
        str(atom_id): sorted({str(card_id) for card_id in card_ids})
        for atom_id, card_ids in atom_to_cards.items()
    }
    normalized_card_to_atoms = {
        str(card_id): sorted({str(atom_id) for atom_id in atom_ids})
        for card_id, atom_ids in card_to_atoms.items()
    }
    if normalized_atom_to_cards != expected_atom_to_cards:
        raise PreviewValidationError("atom_to_cards does not match atoms.jsonl")
    if normalized_card_to_atoms != expected_card_to_atoms:
        raise PreviewValidationError("card_to_atoms does not match atoms.jsonl")


def derive_metrics(preview_dir: Path, previous_dir: Path | None = None) -> dict[str, Any]:
    """Derive all reportable counts from the final preview files."""
    preview_dir = Path(preview_dir)
    scope = read_json(preview_dir / "scope.json")
    candidates = read_jsonl(preview_dir / "candidates.jsonl")
    atoms = read_jsonl(preview_dir / "atoms.jsonl")
    atom_index = read_jsonl(preview_dir / "atom-index.jsonl")
    registry = read_json(preview_dir / "question-registry.json")
    source_map = read_json(preview_dir / "source-map.json")
    scoped_ids = {str(row.get("id", "")) for row in scope.get("cards", [])}
    dispositions = Counter(str(row.get("disposition", "")) for row in candidates)
    statuses = Counter(str(row.get("knowledge_status", "")) for row in atoms)
    transformations = sum(
        1
        for row in candidates
        if str(row.get("transformation_type", "")).strip() not in {"", "无", "None"}
    )
    current_by_id = {str(row["atom_id"]): row for row in atoms}
    previous_path = Path(previous_dir) / "atoms.jsonl" if previous_dir else None
    previous_atoms = read_jsonl(previous_path) if previous_path and previous_path.is_file() else []
    previous_by_id = {str(row["atom_id"]): row for row in previous_atoms}
    current_ids = set(current_by_id)
    previous_ids = set(previous_by_id)
    shared_ids = current_ids & previous_ids
    changes = {
        "added_atoms": len(current_ids - previous_ids),
        "merged_into_existing_atoms": sum(
            1
            for atom_id in shared_ids
            if set(str(value) for value in current_by_id[atom_id].get("source_candidate_ids", []))
            - set(str(value) for value in previous_by_id[atom_id].get("source_candidate_ids", []))
        ),
        "removed_atoms": len(previous_ids - current_ids),
        "unchanged_atoms": sum(
            1
            for atom_id in shared_ids
            if str(current_by_id[atom_id].get("content_hash", ""))
            == str(previous_by_id[atom_id].get("content_hash", ""))
        ),
        "updated_existing_atoms": sum(
            1
            for atom_id in shared_ids
            if str(current_by_id[atom_id].get("content_hash", ""))
            != str(previous_by_id[atom_id].get("content_hash", ""))
        ),
    }
    return {
        "schema_version": 1,
        "generated_from": {
            name: sha256_path(preview_dir / name) for name in METRIC_INPUT_FILES
        },
        "comparison_from": {
            "atoms.jsonl": sha256_path(previous_path)
            if previous_path and previous_path.is_file()
            else ""
        },
        "counts": {
            "scope_cards": len(scope.get("cards", [])),
            "batch_candidates": sum(
                1 for row in candidates if str(row.get("source_card_id", "")) in scoped_ids
            ),
            "candidates": len(candidates),
            "dispositions": dict(sorted(dispositions.items())),
            "atoms": len(atoms),
            "knowledge_status": dict(sorted(statuses.items())),
            "transformations": transformations,
            "atom_index": len(atom_index),
            "question_keys": len(registered_question_keys(registry)),
            "mapped_cards": len(source_map.get("card_to_atoms", {})),
        },
        "changes": changes,
    }


def validate_build_metrics(
    preview_dir: Path, previous_dir: Path | None = None
) -> dict[str, Any]:
    path = Path(preview_dir) / "build-metrics.json"
    actual = read_json(path)
    expected = derive_metrics(preview_dir, previous_dir)
    if actual != expected:
        raise PreviewValidationError("build-metrics.json does not match final preview files")
    return expected


def validate_preview(
    preview_dir: Path, previous_dir: Path | None = None
) -> dict[str, Any]:
    preview_dir = Path(preview_dir)
    scope = read_json(preview_dir / "scope.json")
    candidates = read_jsonl(preview_dir / "candidates.jsonl")
    atoms = read_jsonl(preview_dir / "atoms.jsonl")
    source_map = read_json(preview_dir / "source-map.json")
    atom_index = read_jsonl(preview_dir / "atom-index.jsonl")
    question_registry = read_json(preview_dir / "question-registry.json")
    require_unique(candidates, "candidate_id")
    require_unique(candidates, "candidate_hash")
    require_unique(atoms, "atom_id")
    require_unique(atoms, "content_hash")
    validate_candidate_dispositions(candidates)
    validate_scope_sources(scope, candidates)
    validate_atom_sources(atoms, candidates)
    validate_bidirectional_map(source_map, atoms)
    validate_question_registry(atoms, question_registry)
    validate_atom_index(atom_index, atoms)
    validate_build_metrics(preview_dir, previous_dir)
    return {
        "status": "passed",
        "counts": {
            "candidates": len(candidates),
            "atoms": len(atoms),
            "mapped_cards": len(source_map["card_to_atoms"]),
            "pending_candidates": sum(
                1 for row in candidates if row["disposition"] == "待人工判断"
            ),
            "layer2_only_candidates": sum(
                1 for row in candidates if row["disposition"] == "仅保留二层"
            ),
        },
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--write-metrics", action="store_true")
    args = parser.parse_args()

    try:
        if args.write_metrics:
            metrics = derive_metrics(args.preview, args.previous)
            (args.preview / "build-metrics.json").write_text(
                json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        report = validate_preview(args.preview, args.previous)
        exit_code = 0
    except PreviewValidationError as error:
        report = {"status": "failed", "counts": {}, "errors": [str(error)]}
        exit_code = 1

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
