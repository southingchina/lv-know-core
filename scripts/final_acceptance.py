#!/usr/bin/env python3
"""Accept a promoted Dify mirror and render the sole lv-know final report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import profile_config
from validate_preview import PreviewValidationError, derive_metrics, read_json, read_jsonl, validate_preview


SYNC_MODE_ALIASES = {
    "segment_incremental": "segment_incremental",
    "segments": "segment_incremental",
    "full_document_replace": "full_document_replace",
    "single_document": "full_document_replace",
}


def normalize_sync_mode(value: str) -> str:
    try:
        return SYNC_MODE_ALIASES[value]
    except KeyError as error:
        raise ValueError(f"unsupported sync mode: {value}") from error


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atom_hashes_sha256(master: Path) -> str:
    atoms = read_jsonl(Path(master) / "atoms.jsonl")
    atom_hashes = {str(row["atom_id"]): str(row["content_hash"]) for row in atoms}
    if len(atom_hashes) != len(atoms):
        raise ValueError("duplicate atom id")
    return hashlib.sha256(stable_json(atom_hashes)).hexdigest()


def _add_check(checks: list[dict[str, Any]], check_id: str, passed: bool, detail: str) -> None:
    checks.append({"id": check_id, "passed": bool(passed), "detail": str(detail)})


def _source_stability(master: Path, manifest: Path, vault_root: Path) -> tuple[bool, str]:
    scope = read_json(master / "scope.json")
    if not manifest.is_file():
        return False, "manifest missing"
    if str(scope.get("manifest_sha256", "")).lower() != sha256_path(manifest).lower():
        return False, "manifest hash changed"
    resolved_root = vault_root.resolve()
    for card in scope.get("cards", []):
        source = (resolved_root / str(card["relative_path"])).resolve()
        if not source.is_relative_to(resolved_root):
            return False, f"source path escapes vault: {card.get('id', '')}"
        if not source.is_file():
            return False, f"source missing: {card.get('id', '')}"
        if sha256_path(source).lower() != str(card.get("sha256", "")).lower():
            return False, f"source hash changed: {card.get('id', '')}"
    return True, f"manifest and {len(scope.get('cards', []))} scoped cards unchanged"


def _run_state_consistency(master: Path) -> tuple[bool, str]:
    state = read_json(master / "run-state.json")
    scope = read_json(master / "scope.json")
    atoms = read_jsonl(master / "atoms.jsonl")
    expected_atoms = {
        str(row["atom_id"]): {"version": row["version"], "content_hash": str(row["content_hash"])}
        for row in atoms
    }
    if state.get("atoms") != expected_atoms:
        return False, "run-state atom versions or hashes do not match"
    state_cards = state.get("cards")
    if not isinstance(state_cards, dict):
        return False, "run-state cards missing"
    for card in scope.get("cards", []):
        row = state_cards.get(str(card["id"]), {})
        if str(row.get("sha256", "")).lower() != str(card.get("sha256", "")).lower():
            return False, f"run-state card mismatch: {card.get('id', '')}"
    return True, f"{len(expected_atoms)} atom states and scoped card hashes match"


def _title_uniqueness(master: Path) -> tuple[bool, str]:
    atoms = read_jsonl(master / "atoms.jsonl")
    titles = [str(row.get("title", "")).strip() for row in atoms if str(row.get("title", "")).strip()]
    duplicates = len(titles) - len(set(titles))
    return duplicates == 0, f"duplicate non-empty titles: {duplicates}"


def _snapshots(master: Path, backup_root: Path | None) -> list[Path]:
    parent = Path(backup_root) / master.name if backup_root is not None else master / "snapshots"
    return sorted(parent.glob("*")) if parent.is_dir() else []


def _pipeline_summary(value: dict[str, Any] | None) -> dict[str, int]:
    fields = (
        "input_transcripts",
        "skipped_sources",
        "staged_layer2_cards",
        "committed_layer2_cards",
        "layer2_index_changes",
    )
    source = value or {}
    result: dict[str, int] = {}
    for field in fields:
        count = int(source.get(field, 0) or 0)
        if count < 0:
            raise ValueError(f"pipeline count must not be negative: {field}")
        result[field] = count
    return result


def _segment_sync_ok(
    master: Path,
    sync_evidence: dict[str, Any],
    expected_dataset_name: str | None,
    expected_dataset_id: str | None,
    expected_document_id: str | None,
) -> tuple[bool, bool, str]:
    try:
        evidence_mode = normalize_sync_mode(str(sync_evidence.get("sync_mode") or ""))
    except ValueError:
        evidence_mode = "invalid"
    if expected_dataset_id:
        dataset_ok = sync_evidence.get("dataset_id") == expected_dataset_id
    else:
        dataset_ok = bool(expected_dataset_name) and (
            sync_evidence.get("dataset_name") == expected_dataset_name
        )
    document_ok = (
        expected_document_id is None
        or sync_evidence.get("document_id") == expected_document_id
    )
    control_ok = (
        sync_evidence.get("status") == "passed"
        and evidence_mode == "segment_incremental"
        and dataset_ok
        and document_ok
        and sync_evidence.get("baseline_unchanged") is True
        and sync_evidence.get("mapping_consistent") is True
        and sync_evidence.get("continuation_complete") is True
    )
    local_hashes = {
        str(row["atom_id"]): str(row["content_hash"])
        for row in read_jsonl(master / "atoms.jsonl")
    }
    affected = sync_evidence.get("affected_segments")
    if not isinstance(affected, list):
        return control_ok, False, "affected_segments must be an array"
    seen_atoms: set[str] = set()
    identity_ok = True
    for row in affected:
        if not isinstance(row, dict):
            identity_ok = False
            continue
        atom_id = str(row.get("atom_id") or "")
        if (
            not atom_id
            or atom_id in seen_atoms
            or not str(row.get("segment_id") or "")
            or row.get("status") not in {"completed", "active"}
            or local_hashes.get(atom_id) != str(row.get("content_hash") or "")
        ):
            identity_ok = False
        seen_atoms.add(atom_id)
    return control_ok, identity_ok, f"verified affected segments: {len(affected)}"


def accept(
    master: Path,
    manifest: Path,
    vault_root: Path,
    sync_evidence: dict[str, Any] | None = None,
    backup_root: Path | None = None,
    run_summary: dict[str, Any] | None = None,
    *,
    profile: str = "geo",
    sync_mode: str = "segment_incremental",
    expected_dataset_name: str | None = None,
    expected_document_name: str | None = None,
    expected_dataset_id: str | None = None,
    expected_document_id: str | None = None,
    profile_config_path: Path | str | None = None,
) -> dict[str, Any]:
    master = Path(master)
    manifest = Path(manifest)
    vault_root = Path(vault_root)
    checks: list[dict[str, Any]] = []
    recovered: list[str] = []
    sync_mode = normalize_sync_mode(sync_mode)
    selected_profile = profile_config.load_profile(profile, profile_config_path)
    expected_dataset_name = expected_dataset_name or str(
        selected_profile["dify_dataset"].get("name") or ""
    )
    expected_dataset_id = expected_dataset_id or str(
        selected_profile["dify_dataset"].get("id") or ""
    ) or None
    if sync_mode == "full_document_replace" and expected_document_name is None:
        default_document = profile_config.route_target_document(selected_profile, {})
        expected_document_name = str(default_document.get("document_name") or "")
        expected_document_id = expected_document_id or str(
            default_document.get("document_id") or ""
        ) or None

    try:
        snapshots = _snapshots(master, backup_root)
        previous_dir = snapshots[-1] if snapshots else None
        validation = validate_preview(master, previous_dir)
        _add_check(checks, "local_contract", True, "atoms, indexes, source map and metrics agree")
    except (PreviewValidationError, OSError, ValueError, json.JSONDecodeError) as error:
        validation = {"counts": {}}
        _add_check(checks, "local_contract", False, str(error))

    try:
        passed, detail = _source_stability(master, manifest, vault_root)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        passed, detail = False, str(error)
    _add_check(checks, "source_stability", passed, detail)

    try:
        passed, detail = _run_state_consistency(master)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        passed, detail = False, str(error)
    _add_check(checks, "run_state", passed, detail)

    try:
        passed, detail = _title_uniqueness(master)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        passed, detail = False, str(error)
    _add_check(checks, "title_uniqueness", passed, detail)

    snapshots = _snapshots(master, backup_root)
    _add_check(
        checks,
        "snapshot_evidence",
        True,
        str(snapshots[-1]) if snapshots else "first successful version; no previous master snapshot required",
    )

    try:
        metrics = derive_metrics(master, snapshots[-1] if snapshots else None)
    except (PreviewValidationError, OSError, ValueError, json.JSONDecodeError):
        metrics = {"counts": {}}

    if sync_evidence is None:
        _add_check(checks, "dify_status", True, "Dify not requested for this run")
        dify_status = "未请求"
    else:
        if sync_mode == "segment_incremental":
            sync_ok, identity_ok, identity_detail = _segment_sync_ok(
                master,
                sync_evidence,
                expected_dataset_name,
                expected_dataset_id,
                expected_document_id,
            )
            _add_check(
                checks,
                "dify_status",
                sync_ok,
                "segment baseline, mapping and continuation state agree",
            )
            _add_check(checks, "dify_atom_identity", identity_ok, identity_detail)
        else:
            dataset_ok = (
                expected_dataset_name is None
                or sync_evidence.get("dataset_name") == expected_dataset_name
            )
            document_ok = (
                expected_document_name is None
                or sync_evidence.get("document_name") == expected_document_name
            )
            sync_ok = (
                sync_evidence.get("status") == "passed"
                and dataset_ok
                and document_ok
                and int(sync_evidence.get("remaining_document_count") or 0) == 1
            )
            _add_check(checks, "dify_status", sync_ok, "target dataset unique and one official document remains")
            try:
                expected_count = int(metrics.get("counts", {}).get("atoms") or 0)
                identity_ok = (
                    int(sync_evidence.get("verified_atom_count") or 0) == expected_count
                    and sync_evidence.get("atom_hashes_sha256") == atom_hashes_sha256(master)
                )
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                identity_ok = False
            _add_check(checks, "dify_atom_identity", identity_ok, "remote atom count and marker hash equal local atoms")
        if sync_evidence.get("recovered_remote_correct"):
            recovered.append("同步状态缺失或中断，但线上内容已正确；已补写状态，未重复上传。")
        recovered.extend(str(value) for value in sync_evidence.get("recovered_anomalies", []))
        dify_status = "成功" if sync_ok and identity_ok else "失败"

    status = "passed" if all(row["passed"] for row in checks) else "failed"
    return {
        "schema_version": 1,
        "status": status,
        "counts": metrics.get("counts", validation.get("counts", {})),
        "changes": metrics.get("changes", {}),
        "pipeline": _pipeline_summary(run_summary),
        "dify_status": dify_status,
        "recovered_anomalies": recovered,
        "checks": checks,
    }


def render_report(evidence: dict[str, Any]) -> str:
    counts = evidence.get("counts", {})
    changes = evidence.get("changes", {})
    dispositions = counts.get("dispositions", {})
    statuses = counts.get("knowledge_status", {})
    pipeline = evidence.get("pipeline", {})
    lines = [
        "# lv-know 最终运行报告",
        "",
        "## 结果摘要",
        "",
        f"- 最终状态：{'通过' if evidence.get('status') == 'passed' else '失败'}",
        f"- 输入转录：{pipeline.get('input_transcripts', 0)}",
        f"- 跳过来源：{pipeline.get('skipped_sources', 0)}",
        f"- 临时二层卡：{pipeline.get('staged_layer2_cards', 0)}",
        f"- 正式二层新增：{pipeline.get('committed_layer2_cards', 0)}",
        f"- 二层索引变化：{pipeline.get('layer2_index_changes', 0)}",
        f"- 全库三层知识点：{counts.get('atoms', 0)}",
        f"- 本批范围卡：{counts.get('scope_cards', 0)}",
        f"- 本批候选：{counts.get('batch_candidates', 0)}",
        f"- 新增知识点：{changes.get('added_atoms', 0)}",
        f"- 合并进已有知识点：{changes.get('merged_into_existing_atoms', 0)}",
        f"- 更新已有知识点：{changes.get('updated_existing_atoms', 0)}",
        f"- 未变化知识点：{changes.get('unchanged_atoms', 0)}",
        f"- 候选总量：{counts.get('candidates', 0)}",
        f"- 进入三层：{dispositions.get('进入三层', 0)}",
        f"- 待人工判断：{dispositions.get('待人工判断', 0)}",
        f"- 仅保留二层：{dispositions.get('仅保留二层', 0)}",
        f"- 多人共识：{statuses.get('多人共识', 0)}",
        f"- 争议观点：{statuses.get('争议观点', 0)}",
        f"- 争议总览：{statuses.get('争议总览', 0)}",
        f"- 知识点索引：{counts.get('atom_index', 0)}",
        f"- 稳定问题键：{counts.get('question_keys', 0)}",
        f"- 来源卡映射：{counts.get('mapped_cards', 0)}",
        f"- Dify：{evidence.get('dify_status', '未请求')}",
        "",
        "## 已恢复异常",
        "",
    ]
    recovered = evidence.get("recovered_anomalies", [])
    lines.extend(f"- {value}" for value in recovered) if recovered else lines.append("- 无")
    lines.extend(["", "## 验收明细", "", "| 检查 | 结果 | 说明 |", "|---|---|---|"])
    for row in evidence.get("checks", []):
        detail = str(row.get("detail", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{row.get('id', '')}` | {'通过' if row.get('passed') else '失败'} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def atomic_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--sync-evidence", type=Path)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--run-summary", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--profile", default="geo")
    parser.add_argument("--profile-config", type=Path)
    parser.add_argument(
        "--sync-mode",
        choices=("segment_incremental", "full_document_replace"),
        default="segment_incremental",
    )
    parser.add_argument("--expected-dataset-name")
    parser.add_argument("--expected-document-name")
    parser.add_argument("--expected-dataset-id")
    parser.add_argument("--expected-document-id")
    args = parser.parse_args()
    sync_evidence = read_json(args.sync_evidence) if args.sync_evidence else None
    run_summary = read_json(args.run_summary) if args.run_summary else None
    evidence = accept(
        args.master,
        args.manifest,
        args.vault_root,
        sync_evidence,
        args.backup_root,
        run_summary,
        profile=args.profile,
        sync_mode=args.sync_mode,
        expected_dataset_name=args.expected_dataset_name,
        expected_document_name=args.expected_document_name,
        expected_dataset_id=args.expected_dataset_id,
        expected_document_id=args.expected_document_id,
        profile_config_path=args.profile_config,
    )
    evidence_path = args.evidence or (args.master / "final-acceptance.json")
    report_path = args.report or (args.master / "review-report.md")
    atomic_text(evidence_path, json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    atomic_text(report_path, render_report(evidence))
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
