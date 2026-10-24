from pathlib import Path
import hashlib
import json
import sys
from dataclasses import replace


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import normalize_source_master_metadata as normalizer

from normalize_source_master_metadata import (
    NormalizationError,
    apply_normalization,
    build_manifest_v2_preview,
    plan_normalization,
    planned_markdown_by_id,
    write_normalization_plan,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_ai_legacy_card(tmp_path: Path, card_id: str) -> bytes:
    card = tmp_path / "01-视频母卡" / f"{card_id}.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        "---\n"
        f"id: {card_id}\n"
        "layer: material\n"
        "note_type: source_master\n"
        "eligible_for_l3: true\n"
        f"source_unit_id: source-{card_id}\n"
        "author_id: test-author\n"
        "source_path: source.md\n"
        "source_locator: whole-video\n"
        f"source_unit_sha256: {'a' * 64}\n"
        "primary_theme: AI\n"
        "---\n\n# AI\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "_meta" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text(
        json.dumps(
            {"schema_version": "source-master-v1", "cards": [
                {
                    "id": card_id,
                    "relative_path": f"01-视频母卡/{card_id}.md",
                    "sha256": _sha256(card),
                }
            ]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return card.read_bytes()


def test_plan_lists_legacy_layer_without_modifying_card(tmp_path: Path):
    before = write_ai_legacy_card(tmp_path, "ai-001")

    plan = plan_normalization(tmp_path, tmp_path / "_meta" / "manifest.json")

    assert len(plan) == 1
    assert plan[0].before == {"layer": "material", "source_type": "<missing>"}
    assert plan[0].after == {"layer": 2, "source_type": "short_video"}
    assert before == (tmp_path / "01-视频母卡" / "ai-001.md").read_bytes()


def _plan_file(tmp_path: Path, plan, manifest_v2_path: Path) -> Path:
    plan_path = tmp_path / "preview" / "plan.json"
    write_normalization_plan(
        plan_path,
        vault_root=tmp_path,
        manifest_path=tmp_path / "_meta" / "manifest.json",
        plan=plan,
        manifest_v2_path=manifest_v2_path,
    )
    return plan_path


def test_planned_markdown_requires_hash_verified_plan(tmp_path: Path):
    write_ai_legacy_card(tmp_path, "ai-001")
    plan = plan_normalization(tmp_path, tmp_path / "_meta" / "manifest.json")

    assert "source_type: short_video\n" in planned_markdown_by_id(plan)["ai-001"].replace("\r\n", "\n")

    tampered = [replace(plan[0], planned_file_sha256="0" * 64)]
    try:
        planned_markdown_by_id(tampered)
    except NormalizationError as error:
        assert str(error) == "planned_file_hash_mismatch:ai-001"
    else:
        raise AssertionError("tampered planned markdown was accepted")


def test_apply_refuses_changed_card_baseline_without_writing(tmp_path: Path):
    write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)
    card = tmp_path / "01-视频母卡" / "ai-001.md"
    card.write_bytes(card.read_bytes() + b"changed outside plan\n")

    try:
        apply_normalization(plan_path, tmp_path / "backups")
    except NormalizationError as error:
        assert str(error) == "card_baseline_changed:ai-001"
    else:
        raise AssertionError("changed baseline was applied")

    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "_meta" / "layer3-source-manifest-v2.json").exists()


def test_apply_snapshots_updates_both_values_and_is_idempotent(tmp_path: Path):
    before = write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    generated_v2 = build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)

    result = apply_normalization(plan_path, tmp_path / "backups")

    card = tmp_path / "01-视频母卡" / "ai-001.md"
    planned = planned_markdown_by_id(plan)["ai-001"].encode("utf-8")
    assert result["status"] == "applied"
    assert card.read_bytes() == planned
    assert card.read_bytes().endswith(b"# AI\r\n")
    assert "layer: 2\nsource_type: short_video\n" in card.read_text(encoding="utf-8").replace("\r\n", "\n")
    manifest_after = json.loads(manifest.read_text(encoding="utf-8"))
    assert isinstance(manifest_after, dict)
    assert manifest_after["cards"][0]["sha256"] == _sha256(card)
    assert json.loads((tmp_path / "_meta" / "layer3-source-manifest-v2.json").read_text(encoding="utf-8")) == generated_v2
    snapshot = tmp_path / "backups" / result["run_id"] / "ai-layer2-before"
    assert (snapshot / "01-视频母卡" / "ai-001.md").read_bytes() == before
    assert (snapshot / "_meta" / "manifest.json").exists()
    assert (snapshot / "_meta" / "layer3-source-manifest-v2.json.absent").exists()

    assert apply_normalization(plan_path, tmp_path / "backups")["status"] == "no_changes"


def test_apply_rolls_back_cards_and_both_manifests_after_install_failure(tmp_path: Path, monkeypatch):
    card_before = write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    manifest_before = manifest.read_bytes()
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)
    formal_v2 = tmp_path / "_meta" / "layer3-source-manifest-v2.json"
    formal_v2.write_bytes(b"old manifest v2\n")
    original_write = normalizer._atomic_write_bytes

    attempted = False

    def fail_only_on_v2_install(path: Path, content: bytes) -> None:
        nonlocal attempted
        if path == formal_v2 and not attempted:
            attempted = True
            raise OSError("simulated install failure")
        original_write(path, content)

    monkeypatch.setattr(normalizer, "_atomic_write_bytes", fail_only_on_v2_install)
    try:
        apply_normalization(plan_path, tmp_path / "backups")
    except OSError as error:
        assert str(error) == "simulated install failure"
    else:
        raise AssertionError("failed v2 install did not abort")

    assert (tmp_path / "01-视频母卡" / "ai-001.md").read_bytes() == card_before
    assert manifest.read_bytes() == manifest_before
    assert formal_v2.read_bytes() == b"old manifest v2\n"


def test_apply_reports_incomplete_rollback_after_first_restore_failure(tmp_path: Path, monkeypatch):
    card_before = write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    manifest_before = manifest.read_bytes()
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)
    formal_v2 = tmp_path / "_meta" / "layer3-source-manifest-v2.json"
    formal_v2.write_bytes(b"old manifest v2\n")
    original_write = normalizer._atomic_write_bytes
    original_restore = normalizer._restore_path
    attempted_install = False
    restore_calls: list[Path] = []

    def fail_v2_install(path: Path, content: bytes) -> None:
        nonlocal attempted_install
        if path == formal_v2 and not attempted_install:
            attempted_install = True
            raise OSError("simulated install failure")
        original_write(path, content)

    def fail_first_restore(path: Path, vault_root: Path, snapshot_root: Path) -> None:
        restore_calls.append(path)
        if path.name == "ai-001.md":
            raise OSError("simulated first restore failure")
        original_restore(path, vault_root, snapshot_root)

    monkeypatch.setattr(normalizer, "_atomic_write_bytes", fail_v2_install)
    monkeypatch.setattr(normalizer, "_restore_path", fail_first_restore)
    try:
        apply_normalization(plan_path, tmp_path / "backups")
    except NormalizationError as error:
        assert str(error).startswith("rollback_incomplete:")
        assert "01-视频母卡/ai-001.md" in str(error)
        assert "_meta/manifest.json" in str(error)
        assert "_meta/layer3-source-manifest-v2.json" in str(error)
    else:
        raise AssertionError("incomplete rollback was not reported")

    assert set(restore_calls) == {tmp_path / "01-视频母卡" / "ai-001.md", manifest, formal_v2}
    assert (tmp_path / "01-视频母卡" / "ai-001.md").read_bytes() != card_before
    assert manifest.read_bytes() == manifest_before
    assert formal_v2.read_bytes() == b"old manifest v2\n"


def test_apply_rejects_self_consistent_tampered_planned_body_without_writing(tmp_path: Path):
    card_before = write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    manifest_before = manifest.read_bytes()
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    change = payload["changes"][0]
    change["planned_markdown"] = change["planned_markdown"].replace("# AI", "# tampered AI")
    planned = change["planned_markdown"].encode("utf-8")
    normalized = change["planned_markdown"].lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    change["planned_file_sha256"] = hashlib.sha256(planned).hexdigest()
    change["planned_card_content_sha256"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    tampered_plan = [normalizer.NormalizationChange.from_dict(row) for row in payload["changes"]]
    original_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    payload["planned_manifest_sha256"] = hashlib.sha256(
        normalizer._manifest_bytes_with_planned_hashes(original_manifest, tampered_plan)
    ).hexdigest()
    plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        apply_normalization(plan_path, tmp_path / "backups")
    except NormalizationError as error:
        assert str(error) == "plan_recompute_mismatch:ai-001"
    else:
        raise AssertionError("self-consistent tampered plan was applied")

    assert (tmp_path / "01-视频母卡" / "ai-001.md").read_bytes() == card_before
    assert manifest.read_bytes() == manifest_before
    assert not (tmp_path / "backups").exists()


def test_apply_rejects_hash_rebound_manifest_v2_content_tampering(tmp_path: Path):
    card_before = write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    manifest_before = manifest.read_bytes()
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)
    v2 = json.loads(manifest_v2.read_text(encoding="utf-8"))
    v2["cards"][0]["title"] = "tampered but surface-valid"
    manifest_v2.write_text(json.dumps(v2, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["manifest_v2_sha256"] = _sha256(manifest_v2)
    plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        apply_normalization(plan_path, tmp_path / "backups")
    except NormalizationError as error:
        assert str(error) == "manifest_v2_preview_mismatch"
    else:
        raise AssertionError("tampered v2 preview was applied")

    assert (tmp_path / "01-视频母卡" / "ai-001.md").read_bytes() == card_before
    assert manifest.read_bytes() == manifest_before
    assert not (tmp_path / "backups").exists()


def test_apply_rejects_non_object_manifest_v2_preview_consistently(tmp_path: Path):
    write_ai_legacy_card(tmp_path, "ai-001")
    manifest = tmp_path / "_meta" / "manifest.json"
    plan = plan_normalization(tmp_path, manifest)
    manifest_v2 = tmp_path / "preview" / "source-master-manifest.json"
    build_manifest_v2_preview(tmp_path, manifest, plan, manifest_v2)
    plan_path = _plan_file(tmp_path, plan, manifest_v2)
    manifest_v2.write_text("[]\n", encoding="utf-8")
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    payload["manifest_v2_sha256"] = _sha256(manifest_v2)
    plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    try:
        apply_normalization(plan_path, tmp_path / "backups")
    except NormalizationError as error:
        assert str(error) == "manifest_v2_preview_invalid"
    else:
        raise AssertionError("non-object v2 preview was accepted")
