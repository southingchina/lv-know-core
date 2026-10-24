from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_dify_segments  # noqa: E402


class FakeTransport:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.segments: dict[str, dict] = {}

    def create_segment(self, dataset_id: str, document_id: str, content: str) -> dict:
        segment_id = f"seg-{len(self.segments) + 1}"
        row = {"id": segment_id, "content": content, "enabled": True, "status": "completed"}
        self.created.append(row)
        self.segments[segment_id] = row
        return row

    def update_segment(self, dataset_id: str, document_id: str, segment_id: str, content: str) -> dict:
        row = {"id": segment_id, "content": content, "enabled": True, "status": "completed"}
        self.updated.append(row)
        self.segments[segment_id] = row
        return row

    def get_segment(self, dataset_id: str, document_id: str, segment_id: str) -> dict:
        return self.segments[segment_id]


def atom(atom_id: str, content: str, source_version: str) -> dict:
    return {
        "atom_id": atom_id,
        "content": content,
        "content_hash": sync_dify_segments.sha256_text(content),
        "source_version": source_version,
    }


def test_segment_sync_creates_updates_skips_and_persists_resume_state(tmp_path: Path) -> None:
    transport = FakeTransport()
    mapping_path = tmp_path / "mapping.json"
    receipt_path = tmp_path / "receipt.json"
    mapping_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "ds-1",
                "document_id": "doc-1",
                "baseline_marker": "base-1",
                "atoms": {
                    "same": {"segment_id": "seg-same", "content_hash": sync_dify_segments.sha256_text("相同"), "source_version": "v1"},
                    "changed": {"segment_id": "seg-changed", "content_hash": "0" * 64, "source_version": "v1"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    transport.segments["seg-changed"] = {"id": "seg-changed", "content": "旧", "enabled": True, "status": "completed"}

    result = sync_dify_segments.sync_segments(
        transport=transport,
        dataset_id="ds-1",
        document_id="doc-1",
        atoms=[atom("same", "相同", "v1"), atom("changed", "新版", "v2"), atom("new", "新增", "v1")],
        mapping_path=mapping_path,
        receipt_path=receipt_path,
        expected_baseline="base-1",
        current_baseline="base-1",
        lock_path=tmp_path / "writer.lock",
    )

    assert result["counts"] == {"new": 1, "update": 1, "skip": 1, "failed": 0}
    assert result["sync_mode"] == "segment_incremental"
    saved = json.loads(mapping_path.read_text(encoding="utf-8"))
    assert saved["atoms"]["new"]["segment_id"].startswith("seg-")
    assert saved["atoms"]["changed"]["source_version"] == "v2"
    assert receipt_path.is_file()
    assert not (tmp_path / "writer.lock").exists()


def test_segment_sync_stops_before_writes_on_baseline_drift(tmp_path: Path) -> None:
    transport = FakeTransport()
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps({"schema_version": 1, "dataset_id": "ds-1", "document_id": "doc-1", "baseline_marker": "base-1", "atoms": {}}),
        encoding="utf-8",
    )

    with pytest.raises(sync_dify_segments.SegmentSyncError, match="baseline_changed"):
        sync_dify_segments.sync_segments(
            transport=transport,
            dataset_id="ds-1",
            document_id="doc-1",
            atoms=[atom("new", "新增", "v1")],
            mapping_path=mapping_path,
            receipt_path=tmp_path / "receipt.json",
            expected_baseline="base-1",
            current_baseline="base-2",
            lock_path=tmp_path / "writer.lock",
        )

    assert transport.created == []


def test_resume_after_create_readback_failure_does_not_create_duplicate(tmp_path: Path) -> None:
    class OneReadFailureTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.fail_once = True

        def get_segment(self, dataset_id: str, document_id: str, segment_id: str) -> dict:
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("temporary readback failure")
            return super().get_segment(dataset_id, document_id, segment_id)

    transport = OneReadFailureTransport()
    mapping_path = tmp_path / "mapping.json"
    receipt_path = tmp_path / "receipt.json"
    mapping_path.write_text(
        json.dumps({"schema_version": 1, "dataset_id": "ds-1", "document_id": "doc-1", "baseline_marker": "base-1", "atoms": {}}),
        encoding="utf-8",
    )
    kwargs = dict(
        transport=transport,
        dataset_id="ds-1",
        document_id="doc-1",
        atoms=[atom("new", "新增", "v1")],
        mapping_path=mapping_path,
        receipt_path=receipt_path,
        expected_baseline="base-1",
        current_baseline="base-1",
        lock_path=tmp_path / "writer.lock",
    )

    first = sync_dify_segments.sync_segments(**kwargs)
    assert first["status"] == "failed"
    assert len(transport.created) == 1

    second = sync_dify_segments.sync_segments(**kwargs)
    assert second["status"] == "passed"
    assert len(transport.created) == 1
    assert json.loads(mapping_path.read_text(encoding="utf-8"))["atoms"]["new"]["verification_pending"] is False
