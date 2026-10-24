#!/usr/bin/env python3
"""Guard a staged lv-know run from using stale formal-library baselines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATE_NAME = "run-state.json"
SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_value(name: str, value: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{name} must not be empty")
    return clean


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_state(state_path: Path | str) -> dict[str, Any]:
    path = Path(state_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported run-state schema")
    return data


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def create_state(
    run_dir: Path | str, layer2_hash: str, dify_marker: str
) -> Path:
    layer2_hash = _require_value("layer2_hash", layer2_hash)
    dify_marker = _require_value("dify_marker", dify_marker)
    directory = Path(run_dir)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / STATE_NAME
    if state_path.exists():
        raise FileExistsError(f"run state already exists: {state_path}")
    timestamp = _now()
    state = {
        "schema_version": SCHEMA_VERSION,
        "run_id": directory.name,
        "status": "staging",
        "baselines": {
            "layer2_manifest_sha256": layer2_hash,
            "dify_marker": dify_marker,
        },
        "staged": {
            "layer2_manifest_path": None,
            "layer2_manifest_sha256": None,
            "layer2_card_count": 0,
        },
        "layer2_validation": {"passed": False},
        "layer2_snapshot": {"ready": False, "path": None, "sha256": None},
        "comparison": {"completed": False, "sha256": None},
        "content_review": {"passed": False},
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    _write_json(state_path, state)
    return state_path


def record_staged_manifest(
    state_path: Path | str, staged_manifest_path: Path | str
) -> dict[str, Any]:
    state_file = Path(state_path)
    manifest_file = Path(staged_manifest_path)
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    cards = manifest.get("cards") if isinstance(manifest, dict) else None
    if not isinstance(cards, list):
        raise ValueError("staged manifest must contain a cards array")
    required = {"id", "relative_path", "source_sha256"}
    for index, card in enumerate(cards):
        if not isinstance(card, dict) or not required.issubset(card):
            raise ValueError(f"staged card {index} is missing required identity fields")
    state = _read_state(state_file)
    state["staged"] = {
        "layer2_manifest_path": str(manifest_file.resolve()),
        "layer2_manifest_sha256": sha256_file(manifest_file),
        "layer2_card_count": len(cards),
    }
    state["status"] = "staged_layer2"
    state["updated_at"] = _now()
    _write_json(state_file, state)
    return state["staged"]


def mark_compared(state_path: Path | str, comparison_hash: str) -> dict[str, Any]:
    comparison_hash = _require_value("comparison_hash", comparison_hash)
    state_file = Path(state_path)
    state = _read_state(state_file)
    state["comparison"] = {"completed": True, "sha256": comparison_hash}
    state["content_review"] = {"passed": False}
    state["status"] = "compared"
    state["updated_at"] = _now()
    _write_json(state_file, state)
    return state["comparison"]


def record_content_review(
    state_path: Path | str, review_path: Path | str
) -> dict[str, Any]:
    """Bind a real content-review ledger to the exact current active preview.

    This validates coverage and provenance of review records, not their natural
    language truth. The reviewer must actually read the evidence as the Skill
    requires; quote matching alone is not an allowed review method.
    """
    state_file = Path(state_path)
    state = _read_state(state_file)
    if not state.get("comparison", {}).get("completed"):
        raise ValueError("comparison_not_completed")
    review_file = Path(review_path).resolve()
    review = json.loads(review_file.read_text(encoding="utf-8"))
    if review.get("policy") != "source-fidelity-v1":
        raise ValueError("unsupported_content_review_policy")
    if review.get("comparison_sha256") != state["comparison"]["sha256"]:
        raise ValueError("content_review_comparison_mismatch")
    preview_file = Path(str(review.get("preview_path") or "")).resolve()
    if not preview_file.is_file() or sha256_file(preview_file) != review.get("preview_sha256"):
        raise ValueError("content_review_preview_hash_mismatch")
    preview = [json.loads(line) for line in preview_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not preview:
        raise ValueError("empty_content_review_preview")
    active = {}
    for atom in preview:
        atom_id = str(atom.get("atom_id") or "")
        if not atom_id or atom_id in active or not atom.get("content_hash"):
            raise ValueError("invalid_content_review_preview_identity")
        active[atom_id] = atom["content_hash"]
    entries = review.get("reviews")
    if not isinstance(entries, list):
        raise ValueError("content_review_entries_required")
    indexed = {}
    methods = {"semantic_sample", "semantic_gray", "source_only_extraction", "unchanged_review_reuse"}
    for entry in entries:
        atom_id = str(entry.get("atom_id") or "")
        if not atom_id or atom_id in indexed:
            raise ValueError("duplicate_or_empty_content_review_identity")
        indexed[atom_id] = entry
    for atom_id, content_hash in active.items():
        entry = indexed.get(atom_id)
        if entry is None:
            raise ValueError("active_atom_without_content_review:" + atom_id)
        if entry.get("content_hash") != content_hash:
            raise ValueError("reviewed_atom_hash_mismatch:" + atom_id)
        if entry.get("verdict") != "supported":
            raise ValueError("active_atom_not_supported:" + atom_id)
        if entry.get("method") not in methods or not str(entry.get("reason") or "").strip():
            raise ValueError("content_review_method_and_reason_required:" + atom_id)
    state["content_review"] = {
        "passed": True, "policy": review["policy"],
        "comparison_sha256": review["comparison_sha256"],
        "review_path": str(review_file), "review_sha256": sha256_file(review_file),
        "preview_path": str(preview_file), "preview_sha256": review["preview_sha256"],
        "active_atoms": len(active),
    }
    state["updated_at"] = _now()
    _write_json(state_file, state)
    return state["content_review"]


def record_layer2_validation(
    state_path: Path | str, *, passed: bool
) -> dict[str, Any]:
    state_file = Path(state_path)
    state = _read_state(state_file)
    state["layer2_validation"] = {"passed": bool(passed)}
    state["updated_at"] = _now()
    _write_json(state_file, state)
    return state["layer2_validation"]


def record_layer2_snapshot(
    state_path: Path | str, snapshot_path: Path | str, snapshot_sha256: str
) -> dict[str, Any]:
    state_file = Path(state_path)
    snapshot = Path(snapshot_path)
    expected_hash = _require_value("snapshot_sha256", snapshot_sha256).lower()
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    if sha256_file(snapshot) != expected_hash:
        raise ValueError("snapshot hash mismatch")
    state = _read_state(state_file)
    state["layer2_snapshot"] = {
        "ready": True,
        "path": str(snapshot.resolve()),
        "sha256": expected_hash,
    }
    state["updated_at"] = _now()
    _write_json(state_file, state)
    return state["layer2_snapshot"]


def check_layer2_commit(
    state_path: Path | str, current_layer2_hash: str
) -> dict[str, Any]:
    current_layer2_hash = _require_value("current_layer2_hash", current_layer2_hash)
    state = _read_state(state_path)
    if not state.get("layer2_validation", {}).get("passed"):
        return {"ready": False, "reason": "layer2_validation_not_passed"}
    snapshot = state.get("layer2_snapshot", {})
    snapshot_path = Path(str(snapshot.get("path") or ""))
    if (
        not snapshot.get("ready")
        or not snapshot_path.is_file()
        or sha256_file(snapshot_path) != str(snapshot.get("sha256") or "")
    ):
        return {"ready": False, "reason": "layer2_snapshot_not_ready"}
    if current_layer2_hash != state["baselines"]["layer2_manifest_sha256"]:
        return {"ready": False, "reason": "layer2_baseline_changed"}
    return {"ready": True, "reason": "ready"}


def check_layer3_publish(
    state_path: Path | str, current_layer2_hash: str, current_dify_marker: str
) -> dict[str, Any]:
    current_layer2_hash = _require_value("current_layer2_hash", current_layer2_hash)
    current_dify_marker = _require_value("current_dify_marker", current_dify_marker)
    state = _read_state(state_path)
    if not state.get("comparison", {}).get("completed"):
        return {"ready": False, "reason": "comparison_not_completed"}
    baseline = state["baselines"]
    if current_layer2_hash != baseline["layer2_manifest_sha256"]:
        return {"ready": False, "reason": "layer2_baseline_changed"}
    if current_dify_marker != baseline["dify_marker"]:
        return {"ready": False, "reason": "dify_baseline_changed"}
    if state.get("status") not in {"compared", "commit_pending"}:
        return {"ready": False, "reason": "invalid_run_status"}
    review = state.get("content_review", {})
    if not review.get("passed"):
        return {"ready": False, "reason": "content_review_not_passed"}
    if review.get("comparison_sha256") != state["comparison"]["sha256"]:
        return {"ready": False, "reason": "content_review_not_passed"}
    for kind in ("review", "preview"):
        artifact = Path(str(review.get(kind + "_path") or ""))
        if not artifact.is_file() or sha256_file(artifact) != review.get(kind + "_sha256"):
            return {"ready": False, "reason": "content_review_artifact_changed"}
    return {"ready": True, "reason": "ready"}


def check_commit(
    state_path: Path | str, current_layer2_hash: str, current_dify_marker: str
) -> dict[str, Any]:
    """Compatibility wrapper for the former Layer-3 publication gate."""
    return check_layer3_publish(state_path, current_layer2_hash, current_dify_marker)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init")
    init.add_argument("--run-dir", required=True, type=Path)
    init.add_argument("--layer2-hash", required=True)
    init.add_argument("--dify-marker", required=True)

    staged = commands.add_parser("record-staged")
    staged.add_argument("--state", required=True, type=Path)
    staged.add_argument("--manifest", required=True, type=Path)

    compared = commands.add_parser("mark-compared")
    compared.add_argument("--state", required=True, type=Path)
    compared.add_argument("--comparison-hash", required=True)

    content = commands.add_parser("record-content-review")
    content.add_argument("--state", required=True, type=Path)
    content.add_argument("--review", required=True, type=Path)

    validated = commands.add_parser("record-layer2-validation")
    validated.add_argument("--state", required=True, type=Path)
    validated.add_argument("--passed", action="store_true")

    snapshot = commands.add_parser("record-layer2-snapshot")
    snapshot.add_argument("--state", required=True, type=Path)
    snapshot.add_argument("--snapshot", required=True, type=Path)
    snapshot.add_argument("--sha256", required=True)

    layer2_check = commands.add_parser("check-layer2-commit")
    layer2_check.add_argument("--state", required=True, type=Path)
    layer2_check.add_argument("--layer2-hash", required=True)

    layer3_check = commands.add_parser("check-layer3-publish")
    layer3_check.add_argument("--state", required=True, type=Path)
    layer3_check.add_argument("--layer2-hash", required=True)
    layer3_check.add_argument("--dify-marker", required=True)

    check = commands.add_parser("check-commit")
    check.add_argument("--state", required=True, type=Path)
    check.add_argument("--layer2-hash", required=True)
    check.add_argument("--dify-marker", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "init":
        result: Any = {"state_path": str(
            create_state(args.run_dir, args.layer2_hash, args.dify_marker)
        )}
    elif args.command == "record-staged":
        result = record_staged_manifest(args.state, args.manifest)
    elif args.command == "mark-compared":
        result = mark_compared(args.state, args.comparison_hash)
    elif args.command == "record-content-review":
        result = record_content_review(args.state, args.review)
    elif args.command == "record-layer2-validation":
        result = record_layer2_validation(args.state, passed=args.passed)
    elif args.command == "record-layer2-snapshot":
        result = record_layer2_snapshot(args.state, args.snapshot, args.sha256)
    elif args.command == "check-layer2-commit":
        result = check_layer2_commit(args.state, args.layer2_hash)
    elif args.command == "check-layer3-publish":
        result = check_layer3_publish(args.state, args.layer2_hash, args.dify_marker)
    else:
        result = check_commit(args.state, args.layer2_hash, args.dify_marker)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ready", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
