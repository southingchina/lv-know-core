#!/usr/bin/env python3
"""Deterministic, resumable atom-to-Dify-segment synchronization core.

The transport is injected so production adapters and offline tests share the
same single-writer, baseline, mapping, and read-back rules. Credentials never
enter this module's inputs, mapping, receipt, or logs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Protocol


class SegmentSyncError(RuntimeError):
    pass


class SegmentTransport(Protocol):
    def create_segment(self, dataset_id: str, document_id: str, content: str) -> dict: ...
    def update_segment(self, dataset_id: str, document_id: str, segment_id: str, content: str) -> dict: ...
    def get_segment(self, dataset_id: str, document_id: str, segment_id: str) -> dict: ...


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SegmentSyncError("mapping_must_be_object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _validate_mapping(mapping: dict[str, Any], dataset_id: str, document_id: str) -> dict[str, dict[str, str]]:
    if mapping.get("schema_version") != 1:
        raise SegmentSyncError("unsupported_mapping_schema")
    if mapping.get("dataset_id") != dataset_id or mapping.get("document_id") != document_id:
        raise SegmentSyncError("mapping_target_mismatch")
    atoms = mapping.get("atoms")
    if not isinstance(atoms, dict):
        raise SegmentSyncError("mapping_atoms_must_be_object")
    segment_ids = [str(row.get("segment_id") or "") for row in atoms.values() if isinstance(row, dict)]
    populated = [value for value in segment_ids if value]
    if len(populated) != len(set(populated)):
        raise SegmentSyncError("duplicate_active_segment_mapping")
    return atoms


def _validated_atoms(atoms: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in atoms:
        atom_id = str(raw.get("atom_id") or "").strip()
        content = str(raw.get("content") or "")
        content_hash = str(raw.get("content_hash") or "").lower()
        source_version = str(raw.get("source_version") or "").strip()
        if not atom_id or not content or not source_version:
            raise SegmentSyncError("atom_identity_incomplete")
        if atom_id in seen:
            raise SegmentSyncError("duplicate_atom_id")
        if content_hash != sha256_text(content):
            raise SegmentSyncError(f"content_hash_mismatch:{atom_id}")
        seen.add(atom_id)
        result.append(
            {
                "atom_id": atom_id,
                "content": content,
                "content_hash": content_hash,
                "source_version": source_version,
            }
        )
    return sorted(result, key=lambda row: row["atom_id"])


def _acquire_lock(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise SegmentSyncError("another_writer_active") from error


def _verify_remote(row: dict[str, Any], segment_id: str, content: str) -> None:
    remote_id = str(row.get("id") or row.get("segment_id") or "")
    if remote_id != segment_id:
        raise SegmentSyncError("segment_readback_id_mismatch")
    if str(row.get("content") or "") != content:
        raise SegmentSyncError("segment_readback_content_mismatch")
    if row.get("enabled") is False or row.get("status") not in {None, "completed", "active"}:
        raise SegmentSyncError("segment_readback_not_active")


def sync_segments(
    *,
    transport: SegmentTransport,
    dataset_id: str,
    document_id: str,
    atoms: Iterable[dict[str, Any]],
    mapping_path: Path | str,
    receipt_path: Path | str,
    expected_baseline: str,
    current_baseline: str,
    lock_path: Path | str,
) -> dict[str, Any]:
    """Apply new/update/skip decisions once, persisting each successful atom."""
    mapping_file = Path(mapping_path)
    receipt_file = Path(receipt_path)
    lock_file = Path(lock_path)
    if not expected_baseline or current_baseline != expected_baseline:
        raise SegmentSyncError("baseline_changed")
    mapping = _read_json(mapping_file)
    if mapping.get("baseline_marker") != expected_baseline:
        raise SegmentSyncError("mapping_baseline_mismatch")
    mapped_atoms = _validate_mapping(mapping, dataset_id, document_id)
    work = _validated_atoms(atoms)
    lock_fd = _acquire_lock(lock_file)
    receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "sync_mode": "segment_incremental",
        "dataset_id": dataset_id,
        "document_id": document_id,
        "baseline_unchanged": True,
        "mapping_consistent": True,
        "continuation_complete": False,
        "counts": {"new": 0, "update": 0, "skip": 0, "failed": 0},
        "affected_segments": [],
        "failures": [],
    }
    try:
        for atom in work:
            atom_id = atom["atom_id"]
            previous = mapped_atoms.get(atom_id)
            if (
                isinstance(previous, dict)
                and previous.get("content_hash") == atom["content_hash"]
                and previous.get("source_version") == atom["source_version"]
            ):
                if previous.get("verification_pending"):
                    try:
                        segment_id = str(previous.get("segment_id") or "")
                        remote = transport.get_segment(dataset_id, document_id, segment_id)
                        _verify_remote(remote, segment_id, atom["content"])
                        previous["verification_pending"] = False
                        _write_json(mapping_file, mapping)
                        receipt["affected_segments"].append(
                            {
                                "atom_id": atom_id,
                                "segment_id": segment_id,
                                "status": str(remote.get("status") or "active"),
                                "content_hash": atom["content_hash"],
                                "action": "resume_verify",
                            }
                        )
                    except Exception as error:
                        receipt["counts"]["failed"] += 1
                        receipt["failures"].append(
                            {"atom_id": atom_id, "action": "resume_verify", "error": str(error)}
                        )
                        _write_json(receipt_file, receipt)
                        continue
                receipt["counts"]["skip"] += 1
                continue
            action = "update" if isinstance(previous, dict) and previous.get("segment_id") else "new"
            try:
                if action == "new":
                    response = transport.create_segment(dataset_id, document_id, atom["content"])
                    segment_id = str(response.get("id") or response.get("segment_id") or "")
                    if not segment_id:
                        raise SegmentSyncError("missing_segment_id")
                    mapped_atoms[atom_id] = {
                        "segment_id": segment_id,
                        "content_hash": atom["content_hash"],
                        "source_version": atom["source_version"],
                        "verification_pending": True,
                    }
                    _validate_mapping(mapping, dataset_id, document_id)
                    _write_json(mapping_file, mapping)
                else:
                    segment_id = str(previous["segment_id"])
                    transport.update_segment(dataset_id, document_id, segment_id, atom["content"])
                if not segment_id:
                    raise SegmentSyncError("missing_segment_id")
                remote = transport.get_segment(dataset_id, document_id, segment_id)
                _verify_remote(remote, segment_id, atom["content"])
                mapped_atoms[atom_id] = {
                    "segment_id": segment_id,
                    "content_hash": atom["content_hash"],
                    "source_version": atom["source_version"],
                    "verification_pending": False,
                }
                _validate_mapping(mapping, dataset_id, document_id)
                _write_json(mapping_file, mapping)
                receipt["counts"][action] += 1
                receipt["affected_segments"].append(
                    {
                        "atom_id": atom_id,
                        "segment_id": segment_id,
                        "status": str(remote.get("status") or "active"),
                        "content_hash": atom["content_hash"],
                        "action": action,
                    }
                )
            except Exception as error:
                receipt["counts"]["failed"] += 1
                receipt["failures"].append(
                    {"atom_id": atom_id, "action": action, "error": str(error)}
                )
            _write_json(receipt_file, receipt)
        receipt["continuation_complete"] = receipt["counts"]["failed"] == 0
        receipt["status"] = "passed" if receipt["continuation_complete"] else "failed"
        _write_json(receipt_file, receipt)
        return receipt
    finally:
        os.close(lock_fd)
        lock_file.unlink(missing_ok=True)
