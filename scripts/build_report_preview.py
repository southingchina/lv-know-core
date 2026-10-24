#!/usr/bin/env python3
"""Build a deterministic Layer-3 preview from Layer-2 research-report cards."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


class ReportPreviewError(ValueError):
    """Raised when a report preview input violates the contract."""


def stable_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ReportPreviewError(f"{path.name}:{number} must be an object")
        rows.append(row)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def normalized_text(text: str) -> str:
    return "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", str(text).lower()))


def text_tokens(text: str) -> set[str]:
    normalized = normalized_text(text)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    tokens = {chinese[index : index + 2] for index in range(max(0, len(chinese) - 1))}
    tokens.update(re.findall(r"[a-z0-9]+", normalized))
    return {token for token in tokens if token}


def similarity(left: str, right: str) -> float:
    left_tokens = text_tokens(left)
    right_tokens = text_tokens(right)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, normalized_text(left), normalized_text(right)).ratio()
    return round((0.65 * jaccard) + (0.35 * sequence), 6)


def manifest_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cards"), list):
        rows = payload["cards"]
    else:
        raise ReportPreviewError("manifest must be an array or contain a cards array")
    if not all(isinstance(row, dict) for row in rows):
        raise ReportPreviewError("manifest card rows must be objects")
    return rows


def content_hash(atom: dict[str, Any]) -> str:
    return sha256_bytes(stable_json({key: value for key, value in atom.items() if key != "content_hash"}))


def index_row(atom: dict[str, Any]) -> dict[str, Any]:
    return {
        "atom_id": atom["atom_id"],
        "claim": atom["claim"],
        "content_hash": atom["content_hash"],
        "knowledge_status": atom["knowledge_status"],
        "primary_theme": atom["primary_theme"],
        "question_keys": sorted(atom.get("question_keys", [])),
        "related_themes": sorted(atom.get("related_themes", [])),
        "source_count": len(set(atom["source_card_ids"])),
        "title": atom["title"],
    }


def build_source_map(atoms: list[dict[str, Any]]) -> dict[str, Any]:
    atom_to_cards = {
        str(atom["atom_id"]): sorted({str(value) for value in atom["source_card_ids"]})
        for atom in atoms
    }
    card_to_atoms: dict[str, list[str]] = defaultdict(list)
    for atom_id, card_ids in atom_to_cards.items():
        for card_id in card_ids:
            card_to_atoms[card_id].append(atom_id)
    return {
        "schema_version": 1,
        "card_to_atoms": {
            key: sorted(set(values)) for key, values in sorted(card_to_atoms.items())
        },
        "atom_to_cards": dict(sorted(atom_to_cards.items())),
    }


def recall_matches(
    claim: str, question_keys: list[str], atoms: list[dict[str, Any]], limit: int = 5
) -> list[dict[str, Any]]:
    wanted = set(question_keys)
    keyed = [atom for atom in atoms if wanted & set(atom.get("question_keys", []))]
    pool = keyed if keyed else atoms
    ranked = sorted(
        (
            {
                "atom_id": str(atom["atom_id"]),
                "claim": str(atom["claim"]),
                "score": similarity(claim, str(atom["claim"])),
                "shared_question_keys": sorted(wanted & set(atom.get("question_keys", []))),
            }
            for atom in pool
        ),
        key=lambda row: (-row["score"], row["atom_id"]),
    )
    return ranked[:limit]


def build_preview(
    previous_dir: Path,
    vault_root: Path,
    manifest_path: Path,
    spec_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    previous_dir = Path(previous_dir)
    vault_root = Path(vault_root)
    manifest_path = Path(manifest_path)
    spec_path = Path(spec_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    previous_candidates = read_jsonl(previous_dir / "candidates.jsonl")
    previous_atoms = read_jsonl(previous_dir / "atoms.jsonl")
    registry = read_json(previous_dir / "question-registry.json")
    manifest = manifest_rows(read_json(manifest_path))
    spec = read_json(spec_path)
    if not isinstance(spec, dict) or not isinstance(spec.get("candidates"), list):
        raise ReportPreviewError("spec requires a candidates array")

    manifest_by_id = {str(row.get("id", "")): row for row in manifest}
    scoped_cards: dict[str, dict[str, Any]] = {}
    for item in spec["candidates"]:
        card_id = str(item.get("source_card_id", ""))
        row = manifest_by_id.get(card_id)
        if row is None:
            raise ReportPreviewError(f"report card is missing from manifest: {card_id}")
        relative_path = Path(str(row.get("relative_path", "")))
        card_path = (vault_root / relative_path).resolve()
        if not card_path.is_relative_to(vault_root.resolve()) or not card_path.is_file():
            raise ReportPreviewError(f"report card path is invalid: {card_id}")
        actual_hash = sha256_path(card_path)
        if actual_hash.lower() != str(row.get("sha256", "")).lower():
            raise ReportPreviewError(f"report card hash mismatch: {card_id}")
        claim = str(item.get("claim", "")).strip()
        if not claim or normalized_text(claim) not in normalized_text(card_path.read_text(encoding="utf-8-sig")):
            raise ReportPreviewError(f"candidate claim is not present in its Layer-2 card: {card_id}")
        scoped_cards[card_id] = {
            "id": card_id,
            "title": str(row.get("title", "")),
            "blogger": str(row.get("blogger", "CN-GEO研究报告")),
            "primary_theme": str(row.get("primary_theme", item.get("primary_theme", ""))),
            "related_themes": list(row.get("related_themes", [])),
            "relative_path": str(row["relative_path"]).replace("\\", "/"),
            "sha256": actual_hash,
            "selection_reason": "研究报告章节进入本批结构化处理",
        }

    questions = list(registry.get("questions", []))
    question_by_key = {str(row["question_key"]): row for row in questions}
    for question in spec.get("questions", []):
        key = str(question.get("question_key", "")).strip()
        if not key:
            raise ReportPreviewError("question_key is required")
        if key in question_by_key and question_by_key[key] != question:
            raise ReportPreviewError(f"question key conflicts with the existing registry: {key}")
        question_by_key[key] = question
    questions = [question_by_key[key] for key in sorted(question_by_key)]

    candidates = list(previous_candidates)
    atoms = [dict(row) for row in previous_atoms]
    atom_by_id = {str(row["atom_id"]): row for row in atoms}
    known_candidate_ids = {str(row["candidate_id"]) for row in candidates}
    known_candidate_hashes = {str(row["candidate_hash"]) for row in candidates}
    decisions: list[dict[str, Any]] = []
    added_atoms = 0
    merged_atoms = 0

    for item in spec["candidates"]:
        card_id = str(item["source_card_id"])
        card = scoped_cards[card_id]
        claim = str(item["claim"]).strip()
        question_keys = sorted({str(value) for value in item.get("question_keys", [])})
        if not question_keys or any(key not in question_by_key for key in question_keys):
            raise ReportPreviewError(f"candidate has an unknown question key: {card_id}")
        candidate_payload = {
            "source_card_id": card_id,
            "claim": claim,
            "question_keys": question_keys,
            "source_data_version": str(item.get("source_data_version", "")),
            "source_sample_scope": str(item.get("source_sample_scope", "")),
        }
        candidate_hash = sha256_bytes(stable_json(candidate_payload))
        candidate_id = "cand-" + candidate_hash[:24]
        if candidate_id in known_candidate_ids or candidate_hash in known_candidate_hashes:
            raise ReportPreviewError(f"candidate already exists: {candidate_id}")
        candidate = {
            "answerable_question": str(item["answerable_question"]),
            "boundary": str(item["boundary"]),
            "candidate_hash": candidate_hash,
            "candidate_id": candidate_id,
            "claim": claim,
            "disposition": "进入三层",
            "disposition_reason": "",
            "evidence_status": "报告样本观察",
            "origin": "current-layer2-report-card",
            "primary_theme": str(item["primary_theme"]),
            "prior_atom_id": str(item.get("merge_atom_id", "")),
            "prior_priority": 0,
            "question_keys": question_keys,
            "related_themes": sorted({str(value) for value in item.get("related_themes", [])}),
            "source_blogger": str(card["blogger"]),
            "source_card_id": card_id,
            "source_card_sha256": str(card["sha256"]),
            "source_claim": claim,
            "source_data_version": str(item.get("source_data_version", "")),
            "source_sample_scope": str(item.get("source_sample_scope", "")),
            "source_type": "research_report",
            "transformation_type": "研究报告结构化",
            "type": str(item["type"]),
        }
        candidates.append(candidate)
        known_candidate_ids.add(candidate_id)
        known_candidate_hashes.add(candidate_hash)

        matches = recall_matches(claim, question_keys, previous_atoms)
        merge_atom_id = str(item.get("merge_atom_id", "")).strip()
        if merge_atom_id:
            if merge_atom_id not in atom_by_id:
                raise ReportPreviewError(f"merge target does not exist: {merge_atom_id}")
            if merge_atom_id not in {match["atom_id"] for match in matches}:
                raise ReportPreviewError(f"merge target was not recalled by the index: {merge_atom_id}")
            atom = atom_by_id[merge_atom_id]
            atom["source_card_ids"] = sorted(set(atom["source_card_ids"] + [card_id]))
            atom["source_candidate_ids"] = sorted(
                set(atom["source_candidate_ids"] + [candidate_id])
            )
            atom["version"] = int(atom["version"]) + 1
            atom["content_hash"] = content_hash(atom)
            relation = "merged_existing_atom"
            merged_atoms += 1
            no_merge_reason = ""
        else:
            no_merge_reason = str(item.get("no_merge_reason", "")).strip()
            if not no_merge_reason:
                raise ReportPreviewError(f"new atom requires no_merge_reason: {card_id}")
            atom_id = "atom-" + sha256_bytes(stable_json(["research_report", candidate_hash]))[:20]
            if atom_id in atom_by_id:
                raise ReportPreviewError(f"atom id collision: {atom_id}")
            atom = {
                "answerable_question": str(item["answerable_question"]),
                "atom_id": atom_id,
                "boundary": str(item["boundary"]),
                "claim": claim,
                "knowledge_status": "研究报告观察",
                "primary_theme": str(item["primary_theme"]),
                "question_keys": question_keys,
                "related_atom_ids": [],
                "related_themes": sorted({str(value) for value in item.get("related_themes", [])}),
                "source_candidate_ids": [candidate_id],
                "source_card_ids": [card_id],
                "title": claim.rstrip("。！？!?"),
                "type": str(item["type"]),
                "version": 1,
            }
            atom["content_hash"] = content_hash(atom)
            atoms.append(atom)
            atom_by_id[atom_id] = atom
            relation = "new_atom"
            added_atoms += 1

        decisions.append(
            {
                "candidate_id": candidate_id,
                "source_card_id": card_id,
                "claim": claim,
                "question_keys": question_keys,
                "top_matches": matches,
                "relation": relation,
                "target_atom_id": str(atom["atom_id"]),
                "no_merge_reason": no_merge_reason,
            }
        )

    scope = {
        "schema_version": 1,
        "manifest_sha256": sha256_path(manifest_path),
        "mode": "research_report_batch",
        "count": len(scoped_cards),
        "cards": [scoped_cards[key] for key in sorted(scoped_cards)],
    }
    write_json(output_dir / "scope.json", scope)
    write_jsonl(output_dir / "candidates.jsonl", candidates)
    write_jsonl(output_dir / "atoms.jsonl", atoms)
    write_jsonl(output_dir / "atom-index.jsonl", [index_row(atom) for atom in atoms])
    write_json(
        output_dir / "question-registry.json",
        {"schema_version": 1, "questions": questions},
    )
    write_json(output_dir / "source-map.json", build_source_map(atoms))
    write_json(
        output_dir / "index-recall-report.json",
        {
            "schema_version": 1,
            "method": "question-key-first recall plus lexical similarity; semantic merge remains explicit",
            "batch_cards": len(scoped_cards),
            "batch_candidates": len(spec["candidates"]),
            "decisions": decisions,
        },
    )

    from validate_preview import derive_metrics, validate_preview

    write_json(output_dir / "build-metrics.json", derive_metrics(output_dir, previous_dir))
    validation = validate_preview(output_dir, previous_dir)
    write_json(output_dir / "validation.json", validation)
    result = {
        "status": "passed",
        "scope_cards": len(scoped_cards),
        "batch_candidates": len(spec["candidates"]),
        "added_atoms": added_atoms,
        "merged_into_existing_atoms": merged_atoms,
        "final_atoms": len(atoms),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_preview(
            args.previous, args.vault_root, args.manifest, args.spec, args.output
        )
        exit_code = 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        result = {"status": "failed", "error": str(error)}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
