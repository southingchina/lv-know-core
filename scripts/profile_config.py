"""Load and validate lightweight lv-know knowledge-type profiles."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ROOT = SKILL_ROOT / "profiles"
REQUIRED_FIELDS = {
    "profile_id",
    "display_name",
    "obsidian_vault_root",
    "routes",
    "dify_dataset",
    "target_documents",
    "templates",
    "taxonomy",
    "domain_rules",
}


def _nonempty(value: Any, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"profile {label} must not be empty")
    return clean


def _resolve_resource(config_path: Path, value: Any, label: str) -> str:
    raw = _nonempty(value, label)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (config_path.parent / candidate).resolve()
    if not candidate.is_file():
        raise ValueError(f"profile {label} missing: {candidate}")
    return str(candidate)


def validate_profile(payload: dict[str, Any], config_path: Path) -> dict[str, Any]:
    missing = sorted(REQUIRED_FIELDS - set(payload))
    if missing:
        raise ValueError(f"profile fields missing: {missing}")
    profile = dict(payload)
    profile["profile_id"] = _nonempty(profile["profile_id"], "profile_id")
    profile["display_name"] = _nonempty(profile["display_name"], "display_name")
    profile["obsidian_vault_root"] = _nonempty(
        profile["obsidian_vault_root"], "obsidian_vault_root"
    )

    routes = profile.get("routes")
    if not isinstance(routes, dict) or not routes:
        raise ValueError("profile routes must be a non-empty object")
    for source_type, route in routes.items():
        _nonempty(source_type, "route source_type")
        clean = Path(_nonempty(route, "route path"))
        if clean.is_absolute() or ".." in clean.parts:
            raise ValueError(f"profile route must be relative: {route}")

    dataset = profile.get("dify_dataset")
    if not isinstance(dataset, dict):
        raise ValueError("profile dify_dataset must be an object")
    if not (str(dataset.get("id") or "").strip() or str(dataset.get("name") or "").strip()):
        raise ValueError("profile dify_dataset requires id or name")

    documents = profile.get("target_documents")
    if not isinstance(documents, list) or not documents:
        raise ValueError("profile target_documents must be a non-empty array")
    defaults = 0
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("profile target_documents rows must be objects")
        if not (
            str(document.get("document_id") or "").strip()
            or str(document.get("document_name") or "").strip()
        ):
            raise ValueError("target document requires document_id or document_name")
        rules = document.get("match_rules")
        if not isinstance(rules, dict) or not rules:
            raise ValueError("target document match_rules must be a non-empty object")
        if rules.get("default") is True:
            defaults += 1
    if defaults != 1:
        raise ValueError("profile requires exactly one default target document")

    templates = profile.get("templates")
    if not isinstance(templates, dict):
        raise ValueError("profile templates must be an object")
    profile["templates"] = {
        "layer2": _resolve_resource(config_path, templates.get("layer2"), "templates.layer2"),
        "layer3": _resolve_resource(config_path, templates.get("layer3"), "templates.layer3"),
    }

    taxonomy = profile.get("taxonomy")
    if not isinstance(taxonomy, dict):
        raise ValueError("profile taxonomy must be an object")
    base_tags = taxonomy.get("base_tags")
    allowed_tags = taxonomy.get("allowed_additional_tags")
    if not isinstance(base_tags, list) or not isinstance(allowed_tags, list):
        raise ValueError("profile taxonomy tag fields must be arrays")
    theme_tags = taxonomy.get("theme_tags")
    theme_format = str(taxonomy.get("theme_tag_format") or "").strip()
    if not isinstance(theme_tags, dict) and not theme_format:
        raise ValueError("profile taxonomy requires theme_tags or theme_tag_format")
    _nonempty(taxonomy.get("filename_fallback"), "taxonomy.filename_fallback")

    rules = profile.get("domain_rules")
    if not isinstance(rules, list):
        raise ValueError("profile domain_rules must be an array")
    profile["domain_rules"] = [
        _resolve_resource(config_path, value, "domain_rules") for value in rules
    ]
    profile["config_path"] = str(config_path.resolve())
    return profile


def load_profile(profile_id: str, config_path: Path | str | None = None) -> dict[str, Any]:
    requested = _nonempty(profile_id, "profile_id")
    path = Path(config_path) if config_path is not None else PROFILE_ROOT / f"{requested}.json"
    if not path.is_file():
        raise ValueError(f"unregistered profile: {requested}")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("profile config must contain an object")
    profile = validate_profile(payload, path)
    if profile["profile_id"] != requested:
        raise ValueError(
            f"profile id mismatch: requested {requested}, config has {profile['profile_id']}"
        )
    return profile


def route_for_source(profile: dict[str, Any], source_type: str) -> str:
    routes = profile["routes"]
    route = routes.get(source_type, routes.get("default"))
    if route is None:
        raise ValueError(f"profile route missing for source_type: {source_type}")
    return str(route)


def theme_tag(profile: dict[str, Any], primary_theme: str) -> str:
    taxonomy = profile["taxonomy"]
    mapped = taxonomy.get("theme_tags") or {}
    if primary_theme in mapped:
        return str(mapped[primary_theme])
    pattern = str(taxonomy.get("theme_tag_format") or "").strip()
    if pattern:
        return pattern.format(primary_theme=primary_theme)
    raise ValueError(f"invalid primary_theme: {primary_theme}")


def route_target_document(
    profile: dict[str, Any], atom_metadata: dict[str, Any]
) -> dict[str, Any]:
    default: dict[str, Any] | None = None
    matches: list[dict[str, Any]] = []
    for document in profile["target_documents"]:
        rules = document["match_rules"]
        if rules.get("default") is True:
            default = document
            continue
        if all(atom_metadata.get(key) == value for key, value in rules.items()):
            matches.append(document)
    if len(matches) > 1:
        raise ValueError("atom matches multiple target documents")
    return matches[0] if matches else dict(default or {})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    profile = load_profile(args.profile, args.config)
    print(
        json.dumps(
            {
                "status": "valid",
                "profile_id": profile["profile_id"],
                "target_document_count": len(profile["target_documents"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
