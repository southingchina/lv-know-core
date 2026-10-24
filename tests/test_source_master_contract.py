from pathlib import Path
import sys
import hashlib

SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from source_master_contract import (
    ContractError,
    SemanticVersions,
    build_manifest_v2,
    read_source_master,
    semantic_input_sha256,
    semantic_text,
)


def _card_markdown(
    *,
    source_type: str = "short_video",
    body: str = "原始观点。\n",
    generated: str = "自动观点。\n",
    extra_frontmatter: str = "",
) -> str:
    return (
        "---\n"
        "id: card-001\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-001\n"
        "author_id: author\nsource_type: "
        f"{source_type}\n"
        "source_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n"
        f"{extra_frontmatter}"
        "---\n\n# 标题\n\n"
        f"{body}"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n"
        f"{generated}"
        "<!-- GENERATED_RELATIONS_END -->\n"
    )


def _versions() -> SemanticVersions:
    return SemanticVersions("domain-v1", "questions-v1", "schema-v1")


def test_source_master_excludes_generated_relations(tmp_path: Path):
    card = tmp_path / "01-来源母卡" / "author" / "card.md"
    card.parent.mkdir(parents=True)
    card.write_text(
        "---\n"
        "id: card-001\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-001\n"
        "author_id: author\nblogger: author\nsource_type: short_video\n"
        "source_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n"
        "---\n\n# 标题\n\n<summary>完整原始口播稿</summary>\n\n原始观点。\n"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n自动观点。\n"
        "<!-- GENERATED_RELATIONS_END -->\n",
        encoding="utf-8",
    )

    parsed = read_source_master(card, tmp_path)

    assert "原始观点" in parsed.semantic_text
    assert "自动观点" not in parsed.semantic_text
    assert parsed.frontmatter["layer"] == 2


def test_source_master_rejects_ai_legacy_layer_from_formal_scope(tmp_path: Path):
    card = tmp_path / "card.md"
    card.write_text(
        "---\nid: ai-001\nlayer: material\nnote_type: source_master\n"
        "eligible_for_l3: true\n---\n\n# AI\n",
        encoding="utf-8",
    )

    try:
        read_source_master(card, tmp_path)
    except ContractError as error:
        assert error.code == "legacy_layer_requires_normalization"
    else:
        raise AssertionError("legacy layer entered formal scope")


def test_source_master_rejects_templates_from_formal_scope(tmp_path: Path):
    card = tmp_path / "template.md"
    card.write_text(
        _card_markdown().replace("note_type: source_master", "note_type: template"),
        encoding="utf-8",
    )

    try:
        read_source_master(card, tmp_path)
    except ContractError as error:
        assert error.code == "not_source_master"
    else:
        raise AssertionError("template entered formal scope")


def test_source_master_accepts_short_video_bundle_without_remapping(tmp_path: Path):
    card = tmp_path / "bundle.md"
    card.write_text(
        _card_markdown(
            source_type="short_video_bundle",
            extra_frontmatter=(
                "group_id: GRP-bundle\n"
                "order: 3\n"
                f"context_packet_sha256: {'c' * 64}\n"
            ),
        ),
        encoding="utf-8",
    )

    parsed = read_source_master(card, tmp_path)

    assert parsed.frontmatter["source_type"] == "short_video_bundle"
    assert parsed.frontmatter["group_id"] == "GRP-bundle"


def test_short_video_bundle_context_hash_preserves_group_order_and_packet(
    tmp_path: Path,
):
    card = tmp_path / "bundle.md"
    card.write_text(
        _card_markdown(
            source_type="short_video_bundle",
            extra_frontmatter=(
                "group_id: GRP-bundle\n"
                "order: 3\n"
                f"context_packet_sha256: {'c' * 64}\n"
            ),
        ),
        encoding="utf-8",
    )
    first = read_source_master(card, tmp_path)
    replacements = {
        "group_id: GRP-bundle": "group_id: GRP-changed",
        "order: 3": "order: 4",
        f"context_packet_sha256: {'c' * 64}": f"context_packet_sha256: {'d' * 64}",
    }

    for index, (before, after) in enumerate(replacements.items()):
        changed_path = tmp_path / f"bundle-changed-{index}.md"
        changed_path.write_text(
            card.read_text(encoding="utf-8").replace(before, after),
            encoding="utf-8",
        )
        changed = read_source_master(changed_path, tmp_path)
        assert first.source_context_hash != changed.source_context_hash


def test_source_type_changes_semantic_input_and_cache_identity(tmp_path: Path):
    shared_context = (
        "group_id: GRP-shared\n"
        "order: 3\n"
        f"context_packet_sha256: {'c' * 64}\n"
    )
    bundle_path = tmp_path / "bundle.md"
    bundle_path.write_text(
        _card_markdown(
            source_type="short_video_bundle",
            extra_frontmatter=shared_context,
        ),
        encoding="utf-8",
    )
    bundle = read_source_master(bundle_path, tmp_path)
    bundle_semantic_hash = semantic_input_sha256(bundle, _versions())

    for other_source_type in ("short_video", "long_form"):
        other_path = tmp_path / f"{other_source_type}.md"
        other_path.write_text(
            _card_markdown(
                source_type=other_source_type,
                extra_frontmatter=shared_context,
            ),
            encoding="utf-8",
        )
        other = read_source_master(other_path, tmp_path)
        other_semantic_hash = semantic_input_sha256(other, _versions())

        assert bundle.source_context_hash != other.source_context_hash
        assert bundle_semantic_hash != other_semantic_hash
        assert {"semantic_hashes": [bundle_semantic_hash]} != {
            "semantic_hashes": [other_semantic_hash]
        }


def test_semantic_hash_ignores_generated_relations_and_normalizes_line_endings(
    tmp_path: Path,
):
    lf = tmp_path / "lf.md"
    crlf = tmp_path / "crlf.md"
    generated_change = tmp_path / "generated-change.md"
    source_change = tmp_path / "source-change.md"
    markdown = _card_markdown()
    lf.write_text(markdown, encoding="utf-8", newline="\n")
    crlf.write_text(markdown, encoding="utf-8", newline="\r\n")
    generated_change.write_text(
        _card_markdown(generated="另一条自动观点。\n"), encoding="utf-8"
    )
    source_change.write_text(_card_markdown(body="改动后的原始观点。\n"), encoding="utf-8")

    first = read_source_master(lf, tmp_path)
    crlf_copy = read_source_master(crlf, tmp_path)
    generated_relation_only_change = read_source_master(generated_change, tmp_path)
    source_change_card = read_source_master(source_change, tmp_path)

    assert first.card_content_sha256 == crlf_copy.card_content_sha256
    assert semantic_text(first) == first.semantic_text
    assert semantic_input_sha256(first, _versions()) == semantic_input_sha256(
        generated_relation_only_change, _versions()
    )
    assert first.enrichment_sha256 == generated_relation_only_change.enrichment_sha256
    assert semantic_input_sha256(first, _versions()) != semantic_input_sha256(
        source_change_card, _versions()
    )


def test_planned_markdown_file_hash_preserves_overlay_bom_and_crlf(tmp_path: Path):
    card = tmp_path / "card.md"
    card.write_text(_card_markdown(), encoding="utf-8")
    overlay = "\ufeff" + _card_markdown().replace("\n", "\r\n")

    parsed = read_source_master(card, tmp_path, planned_markdown=overlay)
    normalized_overlay = overlay.lstrip("\ufeff").replace("\r\n", "\n")

    assert parsed.file_sha256 == hashlib.sha256(overlay.encode("utf-8")).hexdigest()
    assert parsed.card_content_sha256 == hashlib.sha256(
        normalized_overlay.encode("utf-8")
    ).hexdigest()


def test_source_master_rejects_unclosed_generated_relations(tmp_path: Path):
    card = tmp_path / "unclosed.md"
    card.write_text(
        _card_markdown().replace("<!-- GENERATED_RELATIONS_END -->\n", ""),
        encoding="utf-8",
    )

    try:
        read_source_master(card, tmp_path)
    except ContractError as error:
        assert error.code == "unbalanced_generated_relations"
    else:
        raise AssertionError("unclosed generated region entered semantic input")


def test_source_master_rejects_stray_generated_relations_end(tmp_path: Path):
    card = tmp_path / "stray-end.md"
    card.write_text(
        _card_markdown().replace(
            "<!-- GENERATED_RELATIONS_BEGIN -->\n自动观点。\n"
            "<!-- GENERATED_RELATIONS_END -->\n",
            "<!-- GENERATED_RELATIONS_END -->\n",
        ),
        encoding="utf-8",
    )

    try:
        read_source_master(card, tmp_path)
    except ContractError as error:
        assert error.code == "unbalanced_generated_relations"
    else:
        raise AssertionError("stray generated end entered semantic input")


def test_enrichment_hash_excludes_details_and_standalone_raw_transcripts(
    tmp_path: Path,
):
    details_one = tmp_path / "details-one.md"
    details_two = tmp_path / "details-two.md"
    standalone_one = tmp_path / "standalone-one.md"
    standalone_two = tmp_path / "standalone-two.md"
    details_one.write_text(
        _card_markdown(
            body=(
                "派生判断。\n\n<details>\n<summary>完整原始口播稿</summary>\n\n"
                '<pre data-lv-know="raw-transcript">原稿甲。</pre>\n</details>\n'
            )
        ),
        encoding="utf-8",
    )
    details_two.write_text(
        details_one.read_text(encoding="utf-8").replace("原稿甲。", "原稿乙。"),
        encoding="utf-8",
    )
    standalone_one.write_text(
        _card_markdown(
            body=(
                "派生判断。\n\n<summary>完整原始口播稿</summary>\n\n"
                '<pre data-lv-know="raw-transcript">原稿甲。</pre>\n\n'
                "## 可保留的派生段落\n派生观点。\n"
            )
        ),
        encoding="utf-8",
    )
    standalone_two.write_text(
        standalone_one.read_text(encoding="utf-8").replace("原稿甲。", "原稿乙。"),
        encoding="utf-8",
    )

    details_first = read_source_master(details_one, tmp_path)
    details_changed = read_source_master(details_two, tmp_path)
    standalone_first = read_source_master(standalone_one, tmp_path)
    standalone_changed = read_source_master(standalone_two, tmp_path)

    assert details_first.enrichment_sha256 == details_changed.enrichment_sha256
    assert standalone_first.enrichment_sha256 == standalone_changed.enrichment_sha256


def test_enrichment_hash_excludes_standalone_raw_transcript_before_generated_region(
    tmp_path: Path,
):
    first_path = tmp_path / "standalone-first.md"
    changed_path = tmp_path / "standalone-changed.md"
    first_path.write_text(
        _card_markdown(
            body="<summary>完整原始口播稿</summary>\n\n原稿甲。\n"
        ),
        encoding="utf-8",
    )
    changed_path.write_text(
        first_path.read_text(encoding="utf-8").replace("原稿甲。", "原稿乙。"),
        encoding="utf-8",
    )

    first = read_source_master(first_path, tmp_path)
    changed = read_source_master(changed_path, tmp_path)

    assert first.enrichment_sha256 == changed.enrichment_sha256


def test_research_report_context_hash_includes_identity_locator_and_data_scope(
    tmp_path: Path,
):
    report = tmp_path / "report.md"
    report.write_text(
        _card_markdown(
            source_type="research_report",
            extra_frontmatter=(
                f"source_sha256: {'b' * 64}\n"
                "data_version: 2026Q2\n"
                "sample_scope: 1000 SME owners\n"
            ),
        ),
        encoding="utf-8",
    )
    first = read_source_master(report, tmp_path)
    replacements = {
        "source_unit_id: src-001": "source_unit_id: report-002",
        "source_locator: whole-file": "source_locator: chapter-2",
        "data_version: 2026Q2": "data_version: 2026Q3",
        "sample_scope: 1000 SME owners": "sample_scope: 2000 SME owners",
    }
    for index, (before, after) in enumerate(replacements.items()):
        changed_path = tmp_path / f"changed-{index}.md"
        changed_path.write_text(
            report.read_text(encoding="utf-8").replace(before, after),
            encoding="utf-8",
        )
        changed = read_source_master(changed_path, tmp_path)
        assert first.source_context_hash != changed.source_context_hash


def test_build_manifest_v2_rejects_duplicate_ids(tmp_path: Path):
    card = tmp_path / "cards" / "card.md"
    card.parent.mkdir()
    card.write_text(_card_markdown(), encoding="utf-8")
    rows = [
        {"id": "card-001", "relative_path": "cards/card.md"},
        {"id": "card-001", "relative_path": "cards/card.md"},
    ]

    try:
        build_manifest_v2(tmp_path, rows, _versions())
    except ContractError as error:
        assert error.code == "duplicate_card_id"
    else:
        raise AssertionError("duplicate manifest id was accepted")


def test_build_manifest_v2_rejects_legacy_row_and_overlay_id_mismatch(tmp_path: Path):
    card = tmp_path / "cards" / "card.md"
    card.parent.mkdir()
    card.write_text(_card_markdown(), encoding="utf-8")
    overlay = _card_markdown().replace("id: card-001", "id: overlay-002")

    try:
        build_manifest_v2(
            tmp_path,
            [{"id": "card-001", "relative_path": "cards/card.md"}],
            _versions(),
            planned_markdown_by_id={"card-001": overlay},
        )
    except ContractError as error:
        assert error.code == "manifest_card_id_mismatch"
    else:
        raise AssertionError("overlay was associated with an inconsistent card id")


def test_build_manifest_v2_rejects_duplicate_emitted_card_ids(tmp_path: Path):
    first = tmp_path / "cards" / "first.md"
    second = tmp_path / "cards" / "second.md"
    first.parent.mkdir()
    first.write_text(_card_markdown(), encoding="utf-8")
    second.write_text(_card_markdown(), encoding="utf-8")

    try:
        build_manifest_v2(
            tmp_path,
            [
                {"id": "legacy-first", "relative_path": "cards/first.md"},
                {"id": "legacy-second", "relative_path": "cards/second.md"},
            ],
            _versions(),
        )
    except ContractError as error:
        assert error.code == "duplicate_emitted_card_id"
    else:
        raise AssertionError("duplicate emitted card id was accepted")
