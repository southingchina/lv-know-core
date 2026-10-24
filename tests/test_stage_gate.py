import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "stage_gate.py"


def load_module():
    spec = importlib.util.spec_from_file_location("stage_gate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_new_run_is_not_commit_ready(tmp_path: Path) -> None:
    gate = load_module()
    state_path = gate.create_state(tmp_path / "run-001", "layer2-a", "marker-1")

    assert gate.check_commit(state_path, "layer2-a", "marker-1") == {
        "ready": False,
        "reason": "comparison_not_completed",
    }


def test_comparison_then_unchanged_baseline_still_needs_content_review(tmp_path: Path) -> None:
    gate = load_module()
    state_path = gate.create_state(tmp_path / "run-001", "layer2-a", "marker-1")
    gate.mark_compared(state_path, "comparison-1")

    assert gate.check_commit(state_path, "layer2-a", "marker-1") == {
        "ready": False,
        "reason": "content_review_not_passed",
    }


@pytest.mark.parametrize(
    ("layer2_hash", "dify_marker", "reason"),
    [
        ("layer2-b", "marker-1", "layer2_baseline_changed"),
        ("layer2-a", "marker-2", "dify_baseline_changed"),
    ],
)
def test_changed_baseline_blocks_commit(
    tmp_path: Path, layer2_hash: str, dify_marker: str, reason: str
) -> None:
    gate = load_module()
    state_path = gate.create_state(tmp_path / "run-001", "layer2-a", "marker-1")
    gate.mark_compared(state_path, "comparison-1")

    assert gate.check_commit(state_path, layer2_hash, dify_marker) == {
        "ready": False,
        "reason": reason,
    }


def test_staged_manifest_is_recorded_without_touching_live_manifest(tmp_path: Path) -> None:
    gate = load_module()
    live_manifest = tmp_path / "live-manifest.json"
    live_manifest.write_text('[{"id":"existing"}]', encoding="utf-8")
    original = live_manifest.read_bytes()
    state_path = gate.create_state(
        tmp_path / "run-001", gate.sha256_file(live_manifest), "marker-1"
    )
    staged_manifest = tmp_path / "staged-manifest.json"
    staged_manifest.write_text(
        json.dumps(
            {
                "cards": [
                    {
                        "id": "author-001",
                        "relative_path": "01-原子知识卡/author/author-001.md",
                        "source_sha256": "a" * 64,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = gate.record_staged_manifest(state_path, staged_manifest)

    assert result["layer2_card_count"] == 1
    assert live_manifest.read_bytes() == original


@pytest.mark.parametrize("layer2_hash,dify_marker", [("", "m"), ("h", "")])
def test_empty_baseline_is_rejected(
    tmp_path: Path, layer2_hash: str, dify_marker: str
) -> None:
    gate = load_module()

    with pytest.raises(ValueError, match="must not be empty"):
        gate.create_state(tmp_path / "run-001", layer2_hash, dify_marker)


def test_layer2_commit_gate_requires_validation_snapshot_and_unchanged_manifest(tmp_path: Path) -> None:
    gate = load_module()
    state_path = gate.create_state(tmp_path / "run-001", "layer2-a", "marker-1")

    assert gate.check_layer2_commit(state_path, "layer2-a")["reason"] == "layer2_validation_not_passed"
    gate.record_layer2_validation(state_path, passed=True)
    assert gate.check_layer2_commit(state_path, "layer2-a")["reason"] == "layer2_snapshot_not_ready"
    snapshot = tmp_path / "snapshot.zip"
    snapshot.write_bytes(b"recoverable")
    gate.record_layer2_snapshot(state_path, snapshot, gate.sha256_file(snapshot))
    assert gate.check_layer2_commit(state_path, "layer2-b")["reason"] == "layer2_baseline_changed"
    assert gate.check_layer2_commit(state_path, "layer2-a") == {"ready": True, "reason": "ready"}


def test_compatibility_alias_cannot_bypass_content_review(tmp_path: Path) -> None:
    gate = load_module()
    state_path = gate.create_state(tmp_path / "run-001", "layer2-a", "marker-1")
    gate.mark_compared(state_path, "comparison-1")

    expected = {"ready": False, "reason": "content_review_not_passed"}
    assert gate.check_layer3_publish(state_path, "layer2-a", "marker-1") == expected
    assert gate.check_commit(state_path, "layer2-a", "marker-1") == expected
