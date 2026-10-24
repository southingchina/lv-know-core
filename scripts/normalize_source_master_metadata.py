from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterable

import yaml

from source_master_contract import SemanticVersions, build_manifest_v2


MISSING = "<missing>"
PLAN_SCHEMA_VERSION = 1
FORMAL_MANIFEST_V2_RELATIVE_PATH = Path("_meta") / "layer3-source-manifest-v2.json"
SEMANTIC_VERSIONS = SemanticVersions(
    "ai-domain-v1", "ai-questions-v1", "source-master-v2"
)
_FRONTMATTER = re.compile(r"^\ufeff?---\r?\n(?P<body>.*?)\r?\n---\r?\n", re.S)
_LAYER_LINE = re.compile(
    r"^(?P<prefix>[ \t]*layer[ \t]*:[ \t]*)(?:material|\"material\"|'material')"
    r"(?P<suffix>[ \t]*(?:#[^\r\n]*)?)(?P<ending>\r?\n)",
    re.M,
)


class NormalizationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizationChange:
    card_id: str
    relative_path: str
    before_file_sha256: str
    planned_file_sha256: str
    planned_card_content_sha256: str
    before: dict[str, Any]
    after: dict[str, Any]
    before_markdown: str
    planned_markdown: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "NormalizationChange":
        return cls(
            card_id=str(value["card_id"]),
            relative_path=str(value["relative_path"]),
            before_file_sha256=str(value["before_file_sha256"]),
            planned_file_sha256=str(value["planned_file_sha256"]),
            planned_card_content_sha256=str(value["planned_card_content_sha256"]),
            before=dict(value["before"]),
            after=dict(value["after"]),
            before_markdown=str(value["before_markdown"]),
            planned_markdown=str(value["planned_markdown"]),
        )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalized_markdown(markdown: str) -> str:
    return markdown.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _card_content_sha256(markdown: str) -> str:
    return hashlib.sha256(_normalized_markdown(markdown).encode("utf-8")).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _semantic_versions_payload() -> dict[str, str]:
    return {
        "domain_policy": SEMANTIC_VERSIONS.domain_policy,
        "question_registry": SEMANTIC_VERSIONS.question_registry,
        "extraction_schema": SEMANTIC_VERSIONS.extraction_schema,
    }


def _load_manifest_rows(manifest_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NormalizationError(f"invalid_manifest:{manifest_path}") from error
    if isinstance(document, list):
        rows = document
    elif isinstance(document, dict) and isinstance(document.get("cards"), list):
        rows = document["cards"]
    else:
        raise NormalizationError("manifest_cards_container_missing")
    if not all(isinstance(row, dict) for row in rows):
        raise NormalizationError("manifest_card_row_invalid")
    return document, rows


def _frontmatter(markdown: str, card_id: str) -> tuple[re.Match[str], dict[str, Any]]:
    match = _FRONTMATTER.match(markdown)
    if not match:
        raise NormalizationError(f"missing_frontmatter:{card_id}")
    parsed = yaml.safe_load(match.group("body")) or {}
    if not isinstance(parsed, dict):
        raise NormalizationError(f"invalid_frontmatter:{card_id}")
    return match, parsed


def _render_normalized_markdown(markdown: str, card_id: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    match, frontmatter = _frontmatter(markdown, card_id)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    layer = frontmatter.get("layer")
    source_type = frontmatter.get("source_type", MISSING)

    if layer == "material":
        before["layer"] = "material"
        after["layer"] = 2
    elif layer != 2:
        raise NormalizationError(f"unsupported_layer:{card_id}:{layer}")
    if source_type == MISSING:
        before["source_type"] = MISSING
        after["source_type"] = "short_video"
    elif source_type != "short_video":
        raise NormalizationError(f"unsupported_source_type:{card_id}:{source_type}")

    if not before:
        return markdown, before, after

    header = markdown[: match.end()]
    body = markdown[match.end() :]
    newline = "\r\n" if "\r\n" in header else "\n"
    if "layer" in before:
        def replace_layer(line: re.Match[str]) -> str:
            insertion = "source_type: short_video" + line.group("ending") if "source_type" in before else ""
            return f"{line.group('prefix')}2{line.group('suffix')}{line.group('ending')}{insertion}"

        header, replaced = _LAYER_LINE.subn(replace_layer, header, count=1)
        if replaced != 1:
            raise NormalizationError(f"layer_scalar_not_replaceable:{card_id}")
    elif "source_type" in before:
        layer_line = re.compile(r"^[ \t]*layer[ \t]*:[^\r\n]*(?:\r?\n)", re.M)
        layer_match = layer_line.search(header)
        if not layer_match:
            raise NormalizationError(f"layer_scalar_not_replaceable:{card_id}")
        header = (
            header[: layer_match.end()]
            + f"source_type: short_video{newline}"
            + header[layer_match.end() :]
        )
    planned = header + body
    _, planned_frontmatter = _frontmatter(planned, card_id)
    for key, expected in after.items():
        if planned_frontmatter.get(key) != expected:
            raise NormalizationError(f"planned_frontmatter_invalid:{card_id}:{key}")
    return planned, before, after


def _inside_vault(vault_root: Path, relative_path: str, card_id: str) -> Path:
    root = vault_root.resolve()
    path = (root / Path(relative_path)).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise NormalizationError(f"relative_path_outside_vault:{card_id}") from error
    return path


def _recompute_change(
    vault_root: Path, card_id: str, relative_path: str
) -> NormalizationChange | None:
    card_path = _inside_vault(vault_root, relative_path, card_id)
    if not card_path.is_file():
        raise NormalizationError(f"card_missing:{card_id}")
    before_bytes = card_path.read_bytes()
    try:
        before_markdown = before_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise NormalizationError(f"card_not_utf8:{card_id}") from error
    planned_markdown, before, after = _render_normalized_markdown(before_markdown, card_id)
    if not before:
        return None
    planned_bytes = planned_markdown.encode("utf-8")
    return NormalizationChange(
        card_id=card_id,
        relative_path=Path(relative_path).as_posix(),
        before_file_sha256=hashlib.sha256(before_bytes).hexdigest(),
        planned_file_sha256=hashlib.sha256(planned_bytes).hexdigest(),
        planned_card_content_sha256=_card_content_sha256(planned_markdown),
        before=before,
        after=after,
        before_markdown=before_markdown,
        planned_markdown=planned_markdown,
    )


def plan_normalization(vault_root: Path, manifest_path: Path) -> list[NormalizationChange]:
    vault_root = vault_root.resolve()
    manifest_path = manifest_path.resolve()
    _, rows = _load_manifest_rows(manifest_path)
    changes: list[NormalizationChange] = []
    seen: set[str] = set()
    for row in rows:
        card_id = str(row.get("id", ""))
        relative_path = str(row.get("relative_path", ""))
        if not card_id or not relative_path:
            raise NormalizationError("manifest_card_identity_missing")
        if card_id in seen:
            raise NormalizationError(f"duplicate_card_id:{card_id}")
        seen.add(card_id)
        change = _recompute_change(vault_root, card_id, relative_path)
        if change is not None:
            changes.append(change)
    return changes


def planned_markdown_by_id(plan: Iterable[NormalizationChange]) -> dict[str, str]:
    overlays: dict[str, str] = {}
    for change in plan:
        if change.card_id in overlays:
            raise NormalizationError(f"duplicate_planned_card_id:{change.card_id}")
        planned_bytes = change.planned_markdown.encode("utf-8")
        if hashlib.sha256(planned_bytes).hexdigest() != change.planned_file_sha256:
            raise NormalizationError(f"planned_file_hash_mismatch:{change.card_id}")
        if _card_content_sha256(change.planned_markdown) != change.planned_card_content_sha256:
            raise NormalizationError(f"planned_card_content_hash_mismatch:{change.card_id}")
        overlays[change.card_id] = change.planned_markdown
    return overlays


def _manifest_bytes_with_planned_hashes(document: Any, plan: Iterable[NormalizationChange]) -> bytes:
    if isinstance(document, list):
        rows = document
    else:
        rows = document["cards"]
    expected = {change.card_id: change.planned_file_sha256 for change in plan}
    updated = 0
    for row in rows:
        card_id = str(row.get("id", ""))
        if card_id in expected:
            row["sha256"] = expected[card_id]
            updated += 1
    if updated != len(expected):
        raise NormalizationError("manifest_plan_id_missing")
    return _canonical_json_bytes(document)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def build_manifest_v2_preview(
    vault_root: Path,
    manifest_path: Path,
    plan: Iterable[NormalizationChange],
    output_path: Path,
) -> dict[str, Any]:
    vault_root = vault_root.resolve()
    manifest_path = manifest_path.resolve()
    plan = list(plan)
    overlays = planned_markdown_by_id(plan)
    _, rows = _load_manifest_rows(manifest_path)
    manifest_v2 = build_manifest_v2(
        vault_root,
        rows,
        SEMANTIC_VERSIONS,
        planned_markdown_by_id=overlays,
    )
    cards = manifest_v2.get("cards", [])
    if len(cards) != len(rows) or any(card.get("layer") != 2 or card.get("source_type") != "short_video" for card in cards):
        raise NormalizationError("manifest_v2_validation_failed")
    _atomic_write_bytes(output_path, _canonical_json_bytes(manifest_v2))
    return manifest_v2


def write_normalization_plan(
    plan_path: Path,
    *,
    vault_root: Path,
    manifest_path: Path,
    plan: Iterable[NormalizationChange],
    manifest_v2_path: Path,
) -> dict[str, Any]:
    plan = list(plan)
    planned_markdown_by_id(plan)
    vault_root = vault_root.resolve()
    manifest_path = manifest_path.resolve()
    manifest_v2_path = manifest_v2_path.resolve()
    if not manifest_v2_path.is_file():
        raise NormalizationError("manifest_v2_preview_missing")
    v2 = json.loads(manifest_v2_path.read_text(encoding="utf-8"))
    if not isinstance(v2, dict) or v2.get("schema_version") != 2 or not isinstance(v2.get("cards"), list):
        raise NormalizationError("manifest_v2_preview_invalid")
    document, _ = _load_manifest_rows(manifest_path)
    planned_manifest_sha256 = hashlib.sha256(_manifest_bytes_with_planned_hashes(document, plan)).hexdigest()
    manifest_relative_path = manifest_path.relative_to(vault_root).as_posix()
    manifest_sha256 = sha256_file(manifest_path)
    run_id = f"ai-metadata-normalization-{manifest_sha256[:12]}"
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "run_id": run_id,
        "vault_root": str(vault_root),
        "manifest_relative_path": manifest_relative_path,
        "manifest_sha256": manifest_sha256,
        "planned_manifest_sha256": planned_manifest_sha256,
        "manifest_v2_preview_path": str(manifest_v2_path),
        "manifest_v2_sha256": sha256_file(manifest_v2_path),
        "semantic_versions": _semantic_versions_payload(),
        "changes": [change.to_dict() for change in plan],
    }
    _atomic_write_bytes(plan_path, _canonical_json_bytes(payload))
    return payload


def atomic_replace_frontmatter_value(card_path: Path, key: str, expected: str, replacement: str) -> None:
    """Atomically replace one exact scalar inside YAML frontmatter."""
    raw = card_path.read_bytes()
    markdown = raw.decode("utf-8")
    match, values = _frontmatter(markdown, card_path.name)
    if values.get(key) != expected:
        raise NormalizationError(f"frontmatter_baseline_changed:{card_path.name}:{key}")
    header = markdown[: match.end()]
    body = markdown[match.end() :]
    pattern = re.compile(
        rf"^(?P<prefix>[ \t]*{re.escape(key)}[ \t]*:[ \t]*)(?:\"?{re.escape(expected)}\"?|'?{re.escape(expected)}'?)"
        r"(?P<suffix>[ \t]*(?:#[^\r\n]*)?)(?P<ending>\r?\n)",
        re.M,
    )
    header, count = pattern.subn(
        lambda item: f"{item.group('prefix')}{replacement}{item.group('suffix')}{item.group('ending')}",
        header,
        count=1,
    )
    if count != 1:
        raise NormalizationError(f"frontmatter_scalar_not_replaceable:{card_path.name}:{key}")
    _atomic_write_bytes(card_path, (header + body).encode("utf-8"))


def _snapshot_path(path: Path, vault_root: Path, snapshot_root: Path) -> None:
    relative = path.resolve().relative_to(vault_root.resolve())
    destination = snapshot_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, destination)
    else:
        marker = destination.with_name(destination.name + ".absent")
        marker.write_text("absent\n", encoding="utf-8")


def _restore_path(path: Path, vault_root: Path, snapshot_root: Path) -> None:
    relative = path.resolve().relative_to(vault_root.resolve())
    source = snapshot_root / relative
    absent = source.with_name(source.name + ".absent")
    if absent.exists():
        path.unlink(missing_ok=True)
    elif source.exists():
        _atomic_write_bytes(path, source.read_bytes())


def _validate_manifest_v2_for_apply(
    payload: dict[str, Any],
    vault_root: Path,
    legacy_rows: list[dict[str, Any]],
    verified_plan: Iterable[NormalizationChange] | None,
) -> bytes:
    preview_path = Path(payload["manifest_v2_preview_path"])
    if not preview_path.is_file() or sha256_file(preview_path) != payload["manifest_v2_sha256"]:
        raise NormalizationError("manifest_v2_preview_changed")
    raw = preview_path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NormalizationError("manifest_v2_preview_invalid") from error
    if not isinstance(document, dict):
        raise NormalizationError("manifest_v2_preview_invalid")
    cards = document.get("cards")
    if document.get("schema_version") != 2 or not isinstance(cards, list):
        raise NormalizationError("manifest_v2_preview_invalid")
    if not all(isinstance(row, dict) for row in cards):
        raise NormalizationError("manifest_v2_preview_invalid")
    expected_ids = {str(row["id"]) for row in legacy_rows}
    if {str(row.get("id")) for row in cards} != expected_ids:
        raise NormalizationError("manifest_v2_card_ids_invalid")
    if any(row.get("layer") != 2 or row.get("source_type") != "short_video" for row in cards):
        raise NormalizationError("manifest_v2_metadata_invalid")
    overlays = planned_markdown_by_id(verified_plan or [])
    try:
        expected_document = build_manifest_v2(
            vault_root,
            legacy_rows,
            SEMANTIC_VERSIONS,
            planned_markdown_by_id=overlays,
        )
    except Exception as error:
        raise NormalizationError("manifest_v2_preview_invalid") from error
    if raw != _canonical_json_bytes(expected_document):
        raise NormalizationError("manifest_v2_preview_mismatch")
    return raw


def _verify_recomputed_plan(
    vault_root: Path, plan: Iterable[NormalizationChange]
) -> list[NormalizationChange]:
    verified: list[NormalizationChange] = []
    for supplied in plan:
        recomputed = _recompute_change(
            vault_root, supplied.card_id, supplied.relative_path
        )
        if recomputed != supplied:
            raise NormalizationError(f"plan_recompute_mismatch:{supplied.card_id}")
        verified.append(recomputed)
    return verified


def _snapshot_matches(path: Path, vault_root: Path, snapshot_root: Path) -> bool:
    relative = path.resolve().relative_to(vault_root.resolve())
    source = snapshot_root / relative
    absent = source.with_name(source.name + ".absent")
    if absent.exists():
        return not path.exists()
    return source.is_file() and path.is_file() and source.read_bytes() == path.read_bytes()


def _rollback_all(
    paths: Iterable[Path], vault_root: Path, snapshot_root: Path
) -> None:
    paths = list(paths)
    failures: list[str] = []
    for path in paths:
        try:
            _restore_path(path, vault_root, snapshot_root)
        except BaseException:
            failures.append(path.relative_to(vault_root).as_posix())
    for path in paths:
        try:
            if not _snapshot_matches(path, vault_root, snapshot_root):
                failures.append(path.relative_to(vault_root).as_posix())
        except BaseException:
            failures.append(path.relative_to(vault_root).as_posix())
    if failures:
        targets = ",".join(path.relative_to(vault_root).as_posix() for path in paths)
        raise NormalizationError(f"rollback_incomplete:{targets}")


def apply_normalization(plan_path: Path, backup_root: Path) -> dict[str, Any]:
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NormalizationError("plan_invalid") from error
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise NormalizationError("plan_schema_invalid")
    if payload.get("semantic_versions") != _semantic_versions_payload():
        raise NormalizationError("semantic_versions_invalid")
    plan = [NormalizationChange.from_dict(row) for row in payload.get("changes", [])]
    planned_markdown_by_id(plan)
    vault_root = Path(payload["vault_root"]).resolve()
    manifest_path = _inside_vault(vault_root, str(payload["manifest_relative_path"]), "manifest")
    formal_v2_path = vault_root / FORMAL_MANIFEST_V2_RELATIVE_PATH
    card_paths = {change.card_id: _inside_vault(vault_root, change.relative_path, change.card_id) for change in plan}
    manifest_document, legacy_rows = _load_manifest_rows(manifest_path)
    all_planned = all(sha256_file(card_paths[change.card_id]) == change.planned_file_sha256 for change in plan)
    if all_planned and sha256_file(manifest_path) == payload["planned_manifest_sha256"] and formal_v2_path.is_file() and sha256_file(formal_v2_path) == payload["manifest_v2_sha256"]:
        _validate_manifest_v2_for_apply(payload, vault_root, legacy_rows, None)
        return {"status": "no_changes", "run_id": payload["run_id"], "change_count": 0}
    if sha256_file(manifest_path) != payload["manifest_sha256"]:
        raise NormalizationError("manifest_baseline_changed")
    for change in plan:
        if sha256_file(card_paths[change.card_id]) != change.before_file_sha256:
            raise NormalizationError(f"card_baseline_changed:{change.card_id}")
    verified_plan = _verify_recomputed_plan(vault_root, plan)
    overlays = planned_markdown_by_id(verified_plan)
    v2_raw = _validate_manifest_v2_for_apply(
        payload, vault_root, legacy_rows, verified_plan
    )
    planned_manifest = _manifest_bytes_with_planned_hashes(manifest_document, verified_plan)
    if hashlib.sha256(planned_manifest).hexdigest() != payload["planned_manifest_sha256"]:
        raise NormalizationError("planned_manifest_hash_mismatch")

    snapshot_root = backup_root.resolve() / str(payload["run_id"]) / "ai-layer2-before"
    if snapshot_root.exists():
        raise NormalizationError("backup_already_exists")
    transaction_paths = [*card_paths.values(), manifest_path, formal_v2_path]
    for path in transaction_paths:
        _snapshot_path(path, vault_root, snapshot_root)
    try:
        for change in verified_plan:
            _atomic_write_bytes(card_paths[change.card_id], overlays[change.card_id].encode("utf-8"))
            if sha256_file(card_paths[change.card_id]) != change.planned_file_sha256:
                raise NormalizationError(f"planned_card_hash_mismatch:{change.card_id}")
        _atomic_write_bytes(manifest_path, planned_manifest)
        _atomic_write_bytes(formal_v2_path, v2_raw)
    except BaseException as operation_error:
        try:
            _rollback_all(transaction_paths, vault_root, snapshot_root)
        except NormalizationError as rollback_error:
            raise rollback_error from operation_error
        raise
    return {"status": "applied", "run_id": payload["run_id"], "change_count": len(plan)}


def _plan_command(arguments: argparse.Namespace) -> dict[str, Any]:
    vault_root = Path(arguments.vault_root)
    manifest_path = Path(arguments.manifest)
    plan = plan_normalization(vault_root, manifest_path)
    manifest_v2 = build_manifest_v2_preview(vault_root, manifest_path, plan, Path(arguments.manifest_v2_output))
    write_normalization_plan(
        Path(arguments.output),
        vault_root=vault_root,
        manifest_path=manifest_path,
        plan=plan,
        manifest_v2_path=Path(arguments.manifest_v2_output),
    )
    return {"status": "passed", "change_count": len(plan), "manifest_v2_card_count": len(manifest_v2["cards"])}


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    plan_parser = subcommands.add_parser("plan")
    plan_parser.add_argument("--vault-root", required=True)
    plan_parser.add_argument("--manifest", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.add_argument("--manifest-v2-output", required=True)
    apply_parser = subcommands.add_parser("apply")
    apply_parser.add_argument("--plan", required=True)
    apply_parser.add_argument("--backup-root", required=True)
    arguments = parser.parse_args()
    if arguments.command == "plan":
        result = _plan_command(arguments)
    else:
        result = apply_normalization(Path(arguments.plan), Path(arguments.backup_root))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
