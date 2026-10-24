#!/usr/bin/env python3
"""Promote a validated Layer 3 preview into the local Dify mirror with rollback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from validate_preview import PreviewValidationError, read_json, read_jsonl, validate_preview


CORE_FILES = (
    "scope.json",
    "candidates.jsonl",
    "atoms.jsonl",
    "atom-index.jsonl",
    "question-registry.json",
    "source-map.json",
    "build-metrics.json",
    "run-state.json",
    "validation.json",
)
OPTIONAL_PROMOTION_FILES = ("manual-approval-delta.json",)
SNAPSHOT_FILES = CORE_FILES + OPTIONAL_PROMOTION_FILES + (
    "dify-single-document-state.json",
    "dify-sync-report.json",
    "final-acceptance.json",
    "review-report.md",
)


class PromotionError(RuntimeError):
    """Raised when a staged preview cannot be safely promoted."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def validate_source_stability(preview_dir: Path, manifest_path: Path, vault_root: Path) -> dict[str, Any]:
    scope = read_json(preview_dir / "scope.json")
    expected_manifest = str(scope.get("manifest_sha256", "")).lower()
    if not expected_manifest:
        raise PromotionError("scope.json is missing manifest_sha256")
    if not manifest_path.is_file():
        raise PromotionError(f"manifest is missing: {manifest_path}")
    if sha256_path(manifest_path).lower() != expected_manifest:
        raise PromotionError("manifest hash changed during the run")

    resolved_root = vault_root.resolve()
    for card in scope.get("cards", []):
        relative_path = Path(str(card["relative_path"]))
        source_path = (resolved_root / relative_path).resolve()
        if not source_path.is_relative_to(resolved_root):
            raise PromotionError(f"source path escapes vault root: {relative_path}")
        if not source_path.is_file():
            raise PromotionError(f"source card is missing: {card['id']}")
        if sha256_path(source_path).lower() != str(card["sha256"]).lower():
            raise PromotionError(f"source hash changed for card {card['id']}")
    return scope


def build_run_state(
    preview_dir: Path,
    target_dir: Path,
    scope: dict[str, Any],
    policy_version: str,
) -> dict[str, Any]:
    previous: dict[str, Any] = {}
    previous_path = target_dir / "run-state.json"
    if previous_path.is_file():
        try:
            previous = read_json(previous_path)
        except PreviewValidationError:
            previous = {}

    cards = dict(previous.get("cards", {})) if isinstance(previous.get("cards", {}), dict) else {}
    for card in scope.get("cards", []):
        cards[str(card["id"])] = {
            "sha256": str(card["sha256"]),
            "relative_path": str(card["relative_path"]),
        }
    atoms = {
        str(atom["atom_id"]): {
            "version": atom["version"],
            "content_hash": str(atom["content_hash"]),
        }
        for atom in read_jsonl(preview_dir / "atoms.jsonl")
    }
    return {
        "schema_version": 1,
        "policy_version": str(policy_version),
        "cards": dict(sorted(cards.items())),
        "atoms": dict(sorted(atoms.items())),
    }


def write_failure_report(preview_dir: Path, run_id: str, error: Exception) -> None:
    text = (
        "# 三层写入失败报告\n\n"
        f"- 运行编号：`{run_id}`\n"
        "- 正式主档：未改动或已回滚\n"
        f"- 错误：{error}\n"
    )
    (preview_dir / "promotion-failure-report.md").write_text(text, encoding="utf-8")


def restore_master(target_dir: Path, snapshot: Path | None, existed: set[str]) -> None:
    for name in SNAPSHOT_FILES:
        destination = target_dir / name
        if name in existed and snapshot is not None and (snapshot / name).is_file():
            restore_file = target_dir / f".{name}.restore"
            shutil.copy2(snapshot / name, restore_file)
            os.replace(restore_file, destination)
        elif name not in existed and destination.exists():
            destination.unlink()


def promote_preview(
    preview_dir: Path,
    target_dir: Path,
    manifest_path: Path,
    vault_root: Path,
    policy_version: str = "2",
    backup_root: Path | None = None,
) -> dict[str, Any]:
    preview_dir = Path(preview_dir)
    target_dir = Path(target_dir)
    manifest_path = Path(manifest_path)
    vault_root = Path(vault_root)
    backup_root = Path(backup_root) if backup_root is not None else None
    run_id = new_run_id()

    try:
        scope = validate_source_stability(preview_dir, manifest_path, vault_root)
        write_json(
            preview_dir / "run-state.json",
            build_run_state(preview_dir, target_dir, scope, policy_version),
        )
        previous_dir = target_dir if (target_dir / "atoms.jsonl").is_file() else None
        validation = validate_preview(preview_dir, previous_dir)
        write_json(preview_dir / "validation.json", validation)
    except (PromotionError, PreviewValidationError, OSError, ValueError) as error:
        write_failure_report(preview_dir, run_id, error)
        raise error if isinstance(error, PromotionError) else PromotionError(str(error)) from error

    target_dir.mkdir(parents=True, exist_ok=True)
    existed = {name for name in SNAPSHOT_FILES if (target_dir / name).is_file()}
    snapshot: Path | None = None
    if existed:
        snapshot_parent = (
            backup_root / target_dir.name if backup_root is not None else target_dir / "snapshots"
        )
        snapshot = snapshot_parent / run_id
        snapshot.mkdir(parents=True, exist_ok=False)
        for name in sorted(existed):
            shutil.copy2(target_dir / name, snapshot / name)

    promotion_files = list(CORE_FILES) + [
        name for name in OPTIONAL_PROMOTION_FILES if (preview_dir / name).is_file()
    ]
    temporary = Path(tempfile.mkdtemp(prefix=f".{target_dir.name}-promotion-", dir=target_dir.parent))
    try:
        for name in promotion_files:
            source = preview_dir / name
            if not source.is_file():
                raise PromotionError(f"promotion source is missing: {name}")
            shutil.copy2(source, temporary / name)
        for name in promotion_files:
            os.replace(temporary / name, target_dir / name)
        for name in OPTIONAL_PROMOTION_FILES:
            if name not in promotion_files and (target_dir / name).exists():
                (target_dir / name).unlink()
    except Exception as error:
        try:
            restore_master(target_dir, snapshot, existed)
        except Exception as restore_error:
            write_failure_report(preview_dir, run_id, restore_error)
            raise PromotionError(
                f"promotion failed and rollback failed: {error}; rollback: {restore_error}"
            ) from restore_error
        write_failure_report(preview_dir, run_id, error)
        raise PromotionError(f"promotion failed: {error}") from error
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    return {
        "status": "passed",
        "run_id": run_id,
        "snapshot": str(snapshot) if snapshot else "",
        "counts": validation["counts"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--policy-version", default="2")
    parser.add_argument("--backup-root", type=Path)
    args = parser.parse_args()
    try:
        result = promote_preview(
            args.preview,
            args.target,
            args.manifest,
            args.vault_root,
            args.policy_version,
            args.backup_root,
        )
        exit_code = 0
    except PromotionError as error:
        result = {"status": "failed", "error": str(error)}
        exit_code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
