from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import yaml

from candidate_contract import model_evidence_segments


GENERATED_BEGIN = "<!-- GENERATED_RELATIONS_BEGIN -->"
GENERATED_END = "<!-- GENERATED_RELATIONS_END -->"
GENERATED_MARKER = re.compile(
    re.escape(GENERATED_BEGIN) + "|" + re.escape(GENERATED_END)
)
RAW_TRANSCRIPT_SUMMARY = (
    r"<summary>(?:原博主校对版口播稿|完整原始口播稿|完整原始正文|原始 ASR[^<]*)</summary>"
)
ALLOWED_SOURCE_TYPES = {
    "short_video",
    "short_video_bundle",
    "long_form",
    "article_or_interview",
    "research_report",
    "unknown",
}


class ContractError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class SemanticVersions:
    domain_policy: str
    question_registry: str
    extraction_schema: str


@dataclass(frozen=True)
class SourceMaster:
    path: Path
    relative_path: str
    frontmatter: dict[str, Any]
    markdown: str
    semantic_text: str
    file_sha256: str
    card_content_sha256: str
    source_unit_sha256: str
    enrichment_sha256: str
    source_context_hash: str

    @property
    def id(self) -> str:
        return str(self.frontmatter["id"])


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _strip_generated(text: str) -> str:
    in_region = False
    for marker in GENERATED_MARKER.finditer(text):
        if marker.group() == GENERATED_BEGIN:
            if in_region:
                raise ContractError("unbalanced_generated_relations", "nested begin")
            in_region = True
        else:
            if not in_region:
                raise ContractError("unbalanced_generated_relations", "stray end")
            in_region = False
    if in_region:
        raise ContractError("unbalanced_generated_relations", "unclosed begin")
    pattern = re.compile(
        re.escape(GENERATED_BEGIN) + r"[\s\S]*?" + re.escape(GENERATED_END)
    )
    return pattern.sub("", text)


def _strip_original_source_blocks(text: str) -> str:
    details = re.compile(
        r"<details>\s*" + RAW_TRANSCRIPT_SUMMARY + r"[\s\S]*?</details>",
        re.I,
    )
    standalone_pre = re.compile(
        RAW_TRANSCRIPT_SUMMARY
        + r"\s*<pre\b(?=[^>]*\bdata-lv-know\s*=\s*[\"']raw-transcript[\"'])"
        + r"[^>]*>[\s\S]*?</pre>",
        re.I,
    )
    standalone_before_generated = re.compile(
        RAW_TRANSCRIPT_SUMMARY
        + r"[\s\S]*?(?="
        + re.escape(GENERATED_BEGIN)
        + r")",
        re.I,
    )
    without_details = details.sub("", text)
    without_standalone_pre = standalone_pre.sub("", without_details)
    return standalone_before_generated.sub("", without_standalone_pre)


def _source_context(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    source_type = str(frontmatter.get("source_type", ""))
    if source_type in {
        "short_video",
        "short_video_bundle",
        "article_or_interview",
        "unknown",
    }:
        if not frontmatter.get("group_id"):
            return {
                "source_type": source_type,
                "context": "independent",
            }
    if source_type in {"short_video", "short_video_bundle", "long_form"}:
        return {
            "source_type": source_type,
            "group_id": frontmatter.get("group_id", ""),
            "order": frontmatter.get("order", ""),
            "context_packet_sha256": frontmatter.get(
                "context_packet_sha256", ""
            ),
        }
    if source_type == "research_report":
        return {
            "source_type": source_type,
            "report_identity": frontmatter.get("source_unit_id", ""),
            "source_sha256": frontmatter.get("source_sha256", ""),
            "section_locator": frontmatter.get("source_locator", ""),
            "data_version": frontmatter.get("data_version", ""),
            "sample_scope": frontmatter.get("sample_scope", ""),
        }
    return {
        "source_type": source_type,
        "context": "independent",
    }


def semantic_text(card: SourceMaster) -> str:
    """Return the source-master body with generated relations excluded."""
    return card.semantic_text


def read_source_master(
    path: Path,
    vault_root: Path,
    *,
    planned_markdown: str | None = None,
) -> SourceMaster:
    raw = path.read_bytes()
    actual_markdown = _normalized(raw.decode("utf-8-sig"))
    markdown = _normalized(planned_markdown) if planned_markdown is not None else actual_markdown
    effective_file_bytes = (
        planned_markdown.encode("utf-8") if planned_markdown is not None else raw
    )
    match = re.match(r"^---\n(.*?)\n---\n", markdown, re.S)
    if not match:
        raise ContractError("missing_frontmatter", str(path))
    frontmatter = yaml.safe_load(match.group(1)) or {}
    if not isinstance(frontmatter, dict):
        raise ContractError("invalid_frontmatter", str(path))
    if frontmatter.get("note_type") != "source_master":
        raise ContractError("not_source_master", str(path))
    if str(frontmatter.get("layer")) == "material":
        raise ContractError("legacy_layer_requires_normalization", str(path))
    if frontmatter.get("layer") != 2:
        raise ContractError("wrong_layer", str(path))
    if frontmatter.get("eligible_for_l3") is not True:
        raise ContractError("not_eligible_for_l3", str(path))
    source_type = str(frontmatter.get("source_type", ""))
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ContractError("unsupported_source_type", source_type)
    required = (
        "id",
        "source_unit_id",
        "source_path",
        "source_locator",
        "source_unit_sha256",
        "primary_theme",
    )
    missing = [key for key in required if not str(frontmatter.get(key, "")).strip()]
    if missing:
        raise ContractError("missing_fields", ",".join(missing))
    if not (frontmatter.get("author_id") or frontmatter.get("blogger")):
        raise ContractError("missing_author", str(path))
    body = markdown[match.end() :]
    clean_body = _strip_generated(body)
    enrichment = _strip_generated(_strip_original_source_blocks(body))
    return SourceMaster(
        path=path,
        relative_path=path.relative_to(vault_root).as_posix(),
        frontmatter=frontmatter,
        markdown=markdown,
        semantic_text=clean_body,
        file_sha256=hashlib.sha256(effective_file_bytes).hexdigest(),
        card_content_sha256=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        source_unit_sha256=str(frontmatter["source_unit_sha256"]).lower(),
        enrichment_sha256=_stable_hash(enrichment),
        source_context_hash=_stable_hash(_source_context(frontmatter)),
    )


def semantic_input_sha256(card: SourceMaster, versions: SemanticVersions) -> str:
    evidence = model_evidence_segments(card)
    # Prefer exactly the source-only projection used for extraction. A legacy
    # card with no recognized evidence remains conservative rather than having
    # arbitrary body edits silently reuse an empty-source cache entry.
    source_input = evidence if evidence else card.semantic_text
    metadata = {
        "author_id": card.frontmatter.get("author_id") or card.frontmatter.get("blogger"),
        "primary_theme": card.frontmatter.get("primary_theme"),
        "related_themes": card.frontmatter.get("related_themes") or [],
        "evidence_status": card.frontmatter.get("evidence_status"),
        "evidence_boundary": card.frontmatter.get("evidence_boundary"),
    }
    return _stable_hash(
        {
            "input_policy": "source-projection-cache-v2",
            "source_unit_id": card.frontmatter.get("source_unit_id"),
            "source_unit_sha256": card.source_unit_sha256,
            "source_evidence_sha256": _stable_hash(source_input),
            "source_metadata": metadata,
            "source_context_hash": card.source_context_hash,
            "domain_policy_version": versions.domain_policy,
            "question_registry_version": versions.question_registry,
            "extraction_schema_version": versions.extraction_schema,
        }
    )


def build_manifest_v2(
    vault_root: Path,
    legacy_rows: list[dict[str, Any]],
    versions: SemanticVersions,
    planned_markdown_by_id: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    overlays = planned_markdown_by_id or {}
    source_cards: list[tuple[str, dict[str, Any], SourceMaster]] = []
    seen: set[str] = set()
    for legacy in legacy_rows:
        card_id = str(legacy["id"])
        if card_id in seen:
            raise ContractError("duplicate_card_id", card_id)
        seen.add(card_id)
        relative_path = str(
            legacy.get("relative_path") or legacy.get("target_relative_path") or ""
        )
        if not relative_path:
            raise ContractError("missing_relative_path", card_id)
        card = read_source_master(
            vault_root / Path(relative_path),
            vault_root,
            planned_markdown=overlays.get(card_id),
        )
        source_cards.append((card_id, legacy, card))

    emitted_ids: set[str] = set()
    for _, _, card in source_cards:
        if card.id in emitted_ids:
            raise ContractError("duplicate_emitted_card_id", card.id)
        emitted_ids.add(card.id)

    cards = []
    for card_id, legacy, card in source_cards:
        if card.id != card_id:
            raise ContractError(
                "manifest_card_id_mismatch", f"{card_id}!={card.id}"
            )
        cards.append(
            {
                "id": card.id,
                "title": str(legacy.get("title") or card.id),
                "blogger": str(
                    card.frontmatter.get("author_id") or card.frontmatter.get("blogger")
                ),
                "primary_theme": str(card.frontmatter["primary_theme"]),
                "related_themes": list(card.frontmatter.get("related_themes") or []),
                "relative_path": card.relative_path,
                "note_type": "source_master",
                "layer": 2,
                "eligible_for_l3": True,
                "source_type": str(card.frontmatter["source_type"]),
                "source_unit_id": str(card.frontmatter["source_unit_id"]),
                "idempotency_key": str(card.frontmatter.get("idempotency_key", "")),
                "source_unit_sha256": card.source_unit_sha256,
                "file_sha256": card.file_sha256,
                "card_content_sha256": card.card_content_sha256,
                "enrichment_sha256": card.enrichment_sha256,
                "source_context_hash": card.source_context_hash,
                "semantic_input_sha256": semantic_input_sha256(card, versions),
            }
        )
    return {
        "schema_version": 2,
        "library_name": vault_root.name,
        "cards": sorted(cards, key=lambda row: row["id"]),
    }
