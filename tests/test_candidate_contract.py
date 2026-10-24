from pathlib import Path
import sys

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from source_master_contract import read_source_master
from candidate_contract import (
    CandidateError,
    audit_card_dispositions,
    fragment_reasons,
    locate_source_claim,
    model_evidence_segments,
    validate_candidate,
)


SEMANTIC_HASH = "b" * 64


@pytest.fixture()
def card(tmp_path: Path):
    path = tmp_path / "card.md"
    path.write_text(
        "---\n"
        "id: card-001\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-001\n"
        "author_id: author\nsource_type: short_video\n"
        "source_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n"
        "---\n\n# 原稿\n\n普通正文也不能成为候选证据。\n\n"
        "## 观点提炼\n\n- 结构化块中生成的总结\n\n"
        "<details>\n<summary>原博主校对版口播稿</summary>\n\n"
        "<p>企业应先验证客户需求，再投入获客预算。</p>\n</details>\n\n"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n生成关联不能作为证据。\n"
        "<!-- GENERATED_RELATIONS_END -->\n",
        encoding="utf-8",
    )
    return read_source_master(path, tmp_path)


@pytest.fixture()
def bundle_card(tmp_path: Path):
    path = tmp_path / "bundle.md"
    path.write_text(
        "---\n"
        "id: bundle-001\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: bundle-src-001\n"
        "author_id: author\nsource_type: short_video_bundle\n"
        "source_path: x.md\nsource_locator: bundle-item:bundle-001\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n"
        "group_id: GRP-bundle\norder: 1\n"
        f"context_packet_sha256: {'c' * 64}\n"
        "---\n\n# Bundle\n\n"
        "普通正文不能成为 bundle 证据。\n\n"
        "## 原素材提炼\n\n"
        "### 原话摘录\n\n"
        "> 企业应先验证客户需求，再投入获客预算。\n\n"
        "^BLK-direct\n\n"
        "原话摘录标题下的普通 prose 也不能成为证据。\n\n"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n"
        "> GENERATED 区中的引文不能成为证据。\n"
        "<!-- GENERATED_RELATIONS_END -->\n\n"
        "### 观点提炼\n\n"
        "> 观点提炼中的 blockquote 不能成为证据。\n\n"
        "### 可直接调用的细节\n\n"
        "> 可直接调用的细节不能成为证据。\n\n"
        "## 二次创作区\n\n"
        "### 原话摘录\n\n"
        "> 派生区域伪造的原话摘录不能成为证据。\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n"
        "<p>bundle 的规范原始 transcript 也是可审计证据。</p>\n</details>\n",
        encoding="utf-8",
    )
    return read_source_master(path, tmp_path)


def _bundle_attack_card(tmp_path: Path, section_markdown: str):
    path = tmp_path / "bundle-attack.md"
    path.write_text(
        "---\n"
        "id: bundle-attack\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: bundle-attack-src\n"
        "author_id: author\nsource_type: short_video_bundle\n"
        "source_path: x.md\nsource_locator: bundle-item:bundle-attack\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n"
        "group_id: GRP-bundle\norder: 1\n"
        f"context_packet_sha256: {'c' * 64}\n"
        "---\n\n# Bundle attack\n\n"
        "## 原素材提炼\n\n"
        f"{section_markdown}",
        encoding="utf-8",
    )
    return read_source_master(path, tmp_path)


def _locator(card, claim: str) -> str:
    match = locate_source_claim(card, claim)
    assert match is not None
    return match.locator


def test_approved_details_may_wrap_canonical_raw_transcript_pre(tmp_path: Path):
    path = tmp_path / "canonical-details-pre.md"
    path.write_text(
        "---\nid: card-canonical-pre\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-canonical-pre\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n\n"
        '<pre data-lv-know="raw-transcript">'
        "企业应先核对需求，再决定是否执行。"
        "</pre>\n\n</details>\n",
        encoding="utf-8",
    )
    canonical = read_source_master(path, tmp_path)

    match = locate_source_claim(canonical, "企业应先核对需求，再决定是否执行。")

    assert match is not None
    assert match.locator.startswith("original:card-lines:")


def test_ai_source_master_full_transcript_summary_is_an_exact_allowlist(
    tmp_path: Path,
):
    path = tmp_path / "ai-source-master.md"
    path.write_text(
        "---\nid: ai-card\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: ai-src\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-video\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: AI基础\n---\n\n"
        "<details>\n<summary>原博主完整转写稿（点击展开）</summary>\n\n"
        "<!-- EMBEDDED_TRANSCRIPT_BEGIN -->\n"
        "Agent 会根据工具结果继续推理。\n"
        "<!-- EMBEDDED_TRANSCRIPT_END -->\n\n</details>\n",
        encoding="utf-8",
    )
    source_master = read_source_master(path, tmp_path)

    allowed = locate_source_claim(source_master, "Agent 会根据工具结果继续推理。")

    assert allowed is not None


def candidate_row(card, **overrides) -> dict:
    claim = "企业应先验证客户需求，再投入获客预算。"
    row = {
        "candidate_id": "cand-001",
        "source_card_id": card.id,
        "source_unit_id": "src-001",
        "source_card_sha256": card.card_content_sha256,
        "semantic_input_sha256": SEMANTIC_HASH,
        "question_key": "customer-validation",
        "claim": claim,
        "source_claim": claim,
        "source_evidence_locator": _locator(card, claim),
        "source_blogger": "author",
        "source_type": "short_video",
        "primary_theme": "01",
        "related_themes": ["02"],
        "evidence_status": "作者观点",
        "boundary": "适用于获客预算决策。",
        "disposition": "进入三层",
        "disposition_reason": "",
    }
    row.update(overrides)
    return row


def test_fragment_claim_cannot_enter_layer3(card):
    candidate = candidate_row(
        card,
        claim="第二呢，其实就是 g 流式获客。",
        disposition="进入三层",
    )
    with pytest.raises(CandidateError, match="fragmentary_claim"):
        validate_candidate(candidate, card, SEMANTIC_HASH)


def test_structured_summary_without_original_evidence_is_rejected(card):
    candidate = candidate_row(
        card,
        source_claim="结构化块中生成的总结",
        source_evidence_locator="观点提炼#2",
        disposition="进入三层",
    )
    with pytest.raises(CandidateError, match="evidence_not_found"):
        validate_candidate(candidate, card, SEMANTIC_HASH)


def test_layer2_only_candidate_is_retained(card):
    candidate = candidate_row(
        card,
        disposition="仅保留二层",
        disposition_reason="纯课程招生信息",
    )
    validate_candidate(candidate, card, SEMANTIC_HASH)


def test_locator_is_deterministic_and_normalizes_whitespace(card):
    match = locate_source_claim(card, "企业应先验证客户需求，\n再投入获客预算。")

    assert match is not None
    assert match.locator.startswith("original:card-lines:")
    assert ":sha256:" in match.locator
    assert card.markdown.splitlines()[match.start_line - 1] == (
        "<p>企业应先验证客户需求，再投入获客预算。</p>"
    )


def test_plain_body_and_derived_summary_are_not_source_evidence(card):
    assert locate_source_claim(card, "普通正文也不能成为候选证据。") is None
    assert locate_source_claim(card, "结构化块中生成的总结") is None


def test_bundle_accepts_direct_quotes_under_exact_heading_path(bundle_card):
    match = locate_source_claim(
        bundle_card,
        "企业应先验证客户需求，再投入获客预算。",
    )

    assert match is not None
    assert bundle_card.markdown.splitlines()[match.start_line - 1] == (
        "> 企业应先验证客户需求，再投入获客预算。"
    )
    assert match.locator.startswith(
        f"original:card-lines:{match.start_line}-{match.end_line}:sha256:"
    )


def test_bundle_accepts_approved_canonical_transcript_container(bundle_card):
    claim = "bundle 的规范原始 transcript 也是可审计证据。"

    match = locate_source_claim(bundle_card, claim)

    assert match is not None
    assert bundle_card.markdown.splitlines()[match.start_line - 1] == (
        f"<p>{claim}</p>"
    )


def test_model_evidence_prefers_one_canonical_transcript_over_duplicate_bundle_quotes(
    bundle_card,
):
    segments = model_evidence_segments(bundle_card)

    assert segments == ("bundle 的规范原始 transcript 也是可审计证据。",)
    assert "企业应先验证客户需求，再投入获客预算。" not in segments


@pytest.mark.parametrize(
    "claim",
    [
        "普通正文不能成为 bundle 证据。",
        "原话摘录标题下的普通 prose 也不能成为证据。",
        "GENERATED 区中的引文不能成为证据。",
        "观点提炼中的 blockquote 不能成为证据。",
        "可直接调用的细节不能成为证据。",
        "派生区域伪造的原话摘录不能成为证据。",
    ],
)
def test_bundle_evidence_parser_fails_closed_outside_exact_quote_path(
    bundle_card, claim: str
):
    assert locate_source_claim(bundle_card, claim) is None


@pytest.mark.parametrize(
    ("section_markdown", "claim"),
    [
        (
            "### 原话摘录\n\n"
            "<details>\n<details>\n"
            "> nested details 中的伪引文。\n"
            "</details>\n</details>\n",
            "nested details 中的伪引文。",
        ),
        (
            "### 原话摘录\n\n<details>\n> 未闭合 details 中的伪引文。\n",
            "未闭合 details 中的伪引文。",
        ),
        (
            "### 原话摘录\n\n<details\n> malformed details 标签后的伪引文。\n",
            "malformed details 标签后的伪引文。",
        ),
        (
            "### 原话摘录\n\n```markdown\n> backtick fence 中的伪引文。\n```\n",
            "backtick fence 中的伪引文。",
        ),
        (
            "### 原话摘录\n\n~~~markdown\n> tilde fence 中的伪引文。\n~~~\n",
            "tilde fence 中的伪引文。",
        ),
        (
            "### 原话摘录\n\n<!--\n> HTML comment 中的伪引文。\n-->\n",
            "HTML comment 中的伪引文。",
        ),
        (
            "    ### 原话摘录\n\n    > 四空格 fake heading 和 quote。\n",
            "四空格 fake heading 和 quote。",
        ),
        (
            "### 原话摘录\n\n    > 四空格缩进 code block 中的伪引文。\n",
            "四空格缩进 code block 中的伪引文。",
        ),
        (
            "### 原话摘录\n\n"
            "<!-- GENERATED_RELATIONS_BEGIN -->\n"
            "> GENERATED 仍必须排除。\n"
            "<!-- GENERATED_RELATIONS_END -->\n",
            "GENERATED 仍必须排除。",
        ),
    ],
)
def test_bundle_parser_rejects_markdown_container_attacks(
    tmp_path: Path, section_markdown: str, claim: str
):
    attacked = _bundle_attack_card(tmp_path, section_markdown)

    assert locate_source_claim(attacked, claim) is None


@pytest.mark.parametrize(
    ("section_markdown", "claim"),
    [
        (
            "<details\n### 原话摘录\n> malformed opener 跨 heading 恢复绕过。\n",
            "malformed opener 跨 heading 恢复绕过。",
        ),
        (
            "</details\n### 原话摘录\n> malformed closer 跨 heading 恢复绕过。\n",
            "malformed closer 跨 heading 恢复绕过。",
        ),
        (
            "<details\n"
            "## 其他章节\n"
            "## 原素材提炼\n"
            "### 原话摘录\n"
            "> 后续合法章节不能恢复 poisoned details。\n",
            "后续合法章节不能恢复 poisoned details。",
        ),
        (
            "<details\n"
            "<!-- GENERATED_RELATIONS_BEGIN -->\n"
            "</details>\n"
            "<!-- GENERATED_RELATIONS_END -->\n"
            "### 原话摘录\n"
            "> GENERATED 区不能恢复 poisoned details。\n",
            "GENERATED 区不能恢复 poisoned details。",
        ),
    ],
)
def test_bundle_malformed_details_permanently_poison_remaining_card(
    tmp_path: Path, section_markdown: str, claim: str
):
    attacked = _bundle_attack_card(tmp_path, section_markdown)

    assert locate_source_claim(attacked, claim) is None


def test_bundle_balanced_details_allows_later_direct_quote_with_locator(
    tmp_path: Path,
):
    claim = "balanced details 外的真实 direct blockquote。"
    card = _bundle_attack_card(
        tmp_path,
        "<details>\n"
        "<summary>编辑说明</summary>\n"
        "容器内正文不作为证据。\n"
        "</details>\n"
        "### 原话摘录\n"
        f"> {claim}\n",
    )

    match = locate_source_claim(card, claim)

    assert match is not None
    assert card.markdown.splitlines()[match.start_line - 1] == f"> {claim}"
    assert match.locator.startswith(
        f"original:card-lines:{match.start_line}-{match.end_line}:sha256:"
    )


@pytest.mark.parametrize("marker", ["```", "~~~"])
def test_bundle_fence_cannot_close_across_quoted_to_top_level_prefix(
    tmp_path: Path, marker: str
):
    claim = f"{marker[0]} quoted-open top-level-close 绕过。"
    attacked = _bundle_attack_card(
        tmp_path,
        f"### 原话摘录\n\n> {marker}md\n{marker}\n> {claim}\n",
    )

    assert locate_source_claim(attacked, claim) is None


@pytest.mark.parametrize("marker", ["```", "~~~"])
def test_bundle_fence_prefix_switch_poison_survives_later_top_level_close(
    tmp_path: Path, marker: str
):
    claim = f"{marker[0]} top-level-open quoted-close 绕过。"
    attacked = _bundle_attack_card(
        tmp_path,
        f"### 原话摘录\n\n{marker}md\n> {marker}\n{marker}\n> {claim}\n",
    )

    assert locate_source_claim(attacked, claim) is None


def test_bundle_parser_rejects_tab_indented_fake_heading_and_quote(tmp_path: Path):
    claim = "Tab 缩进 code block 中的伪引文。"
    attacked = _bundle_attack_card(
        tmp_path,
        f"\t### 原话摘录\n\n\t> {claim}\n",
    )

    assert locate_source_claim(attacked, claim) is None


@pytest.mark.parametrize("indent", [" ", "  ", "   "])
def test_bundle_accepts_commonmark_direct_quote_indentation_and_locator(
    tmp_path: Path, indent: str
):
    claim = f"{len(indent)} 空格真实 direct blockquote。"
    card = _bundle_attack_card(
        tmp_path,
        f"### 原话摘录\n\n{indent}> {claim}\n",
    )

    match = locate_source_claim(card, claim)

    assert match is not None
    assert card.markdown.splitlines()[match.start_line - 1] == f"{indent}> {claim}"
    assert match.locator.startswith(
        f"original:card-lines:{match.start_line}-{match.end_line}:sha256:"
    )


def test_locator_does_not_join_two_source_segments(tmp_path: Path):
    path = tmp_path / "segmented.md"
    path.write_text(
        "---\nid: card-segments\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-segments\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n<p>甲</p>\n</details>\n\n"
        "## 任意派生标题\n乙\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n<p>乙</p>\n</details>\n",
        encoding="utf-8",
    )
    segmented = read_source_master(path, tmp_path)

    assert locate_source_claim(segmented, "甲乙") is None
    assert locate_source_claim(segmented, "甲") is not None
    assert locate_source_claim(segmented, "乙") is not None


def test_details_with_raw_keyword_but_unapproved_first_summary_is_rejected(
    tmp_path: Path,
):
    path = tmp_path / "unapproved-details.md"
    path.write_text(
        "---\nid: card-unapproved\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-unapproved\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "<details>\n<summary>编辑备注</summary>\n"
        "这里提到原始 ASR，但它不是认可原文容器。\n</details>\n",
        encoding="utf-8",
    )
    unapproved = read_source_master(path, tmp_path)

    assert (
        locate_source_claim(unapproved, "这里提到原始 ASR，但它不是认可原文容器。")
        is None
    )


def test_unclosed_approved_details_is_not_an_evidence_container(tmp_path: Path):
    path = tmp_path / "unclosed-details.md"
    path.write_text(
        "---\nid: card-unclosed-details\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-unclosed-details\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n"
        "未闭合 details 后的派生正文不能成为证据。\n",
        encoding="utf-8",
    )
    malformed = read_source_master(path, tmp_path)

    assert (
        locate_source_claim(malformed, "未闭合 details 后的派生正文不能成为证据。")
        is None
    )


def test_unclosed_raw_transcript_pre_is_not_an_evidence_container(tmp_path: Path):
    path = tmp_path / "unclosed-pre.md"
    path.write_text(
        "---\nid: card-unclosed-pre\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-unclosed-pre\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        '<pre data-lv-know="raw-transcript">\n'
        "未闭合 pre 后的派生正文不能成为证据。\n",
        encoding="utf-8",
    )
    malformed = read_source_master(path, tmp_path)

    assert (
        locate_source_claim(malformed, "未闭合 pre 后的派生正文不能成为证据。") is None
    )


def test_nested_details_fail_closed_before_later_standalone_summary(tmp_path: Path):
    path = tmp_path / "nested-details-attack.md"
    path.write_text(
        "---\nid: card-nested-details\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-nested-details\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "<details>\n<summary>完整原始口播稿</summary>\n"
        "<details>\n<summary>编辑备注</summary>\n内层内容\n</details>\n"
        "<summary>完整原始口播稿</summary>\n<p>内层关闭后的绕过正文。</p>\n"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n派生内容\n<!-- GENERATED_RELATIONS_END -->\n",
        encoding="utf-8",
    )
    attacked = read_source_master(path, tmp_path)

    assert locate_source_claim(attacked, "内层关闭后的绕过正文。") is None


def test_nested_raw_pre_fails_closed_before_later_pre_and_summary(tmp_path: Path):
    path = tmp_path / "nested-pre-attack.md"
    path.write_text(
        "---\nid: card-nested-pre\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-nested-pre\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        '<pre data-lv-know="raw-transcript">\n'
        '<pre data-lv-know="raw-transcript">\n内层内容\n</pre>\n'
        '<pre data-lv-know="raw-transcript">后续 pre 绕过正文。</pre>\n'
        "<summary>完整原始口播稿</summary>\n<p>后续 summary 绕过正文。</p>\n"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n派生内容\n<!-- GENERATED_RELATIONS_END -->\n",
        encoding="utf-8",
    )
    attacked = read_source_master(path, tmp_path)

    assert locate_source_claim(attacked, "后续 pre 绕过正文。") is None
    assert locate_source_claim(attacked, "后续 summary 绕过正文。") is None


def test_ascii_word_boundary_is_not_erased(card):
    markdown = card.markdown.replace(
        "企业应先验证客户需求，再投入获客预算。", "使用 AI Agent 处理任务。"
    )
    path = card.path.parent / "ascii-boundary.md"
    path.write_text(markdown, encoding="utf-8")
    ascii_card = read_source_master(path, card.path.parent)

    assert locate_source_claim(ascii_card, "使用 AI Agent 处理任务。") is not None
    assert locate_source_claim(ascii_card, "使用 AIAgent 处理任务。") is None


def test_standalone_recognized_raw_summary_is_a_source_container(tmp_path: Path):
    path = tmp_path / "standalone-raw.md"
    path.write_text(
        "---\nid: card-standalone\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: src-standalone\nauthor_id: author\n"
        "source_type: short_video\nsource_path: x.md\nsource_locator: whole-file\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "普通正文不能作为证据。\n\n<summary>完整原始口播稿</summary>\n"
        "<p>独立原文容器支持这个判断。</p>\n"
        "<!-- GENERATED_RELATIONS_BEGIN -->\n派生内容\n<!-- GENERATED_RELATIONS_END -->\n"
        "end 后的派生正文也不能成为证据。\n",
        encoding="utf-8",
    )
    standalone = read_source_master(path, tmp_path)

    assert locate_source_claim(standalone, "独立原文容器支持这个判断。") is not None
    assert locate_source_claim(standalone, "普通正文不能作为证据。") is None
    assert locate_source_claim(standalone, "end 后的派生正文也不能成为证据。") is None


def test_research_reports_only_accept_explicit_quote_section(tmp_path: Path):
    path = tmp_path / "report.md"
    path.write_text(
        "---\nid: report-001\nlayer: 2\nnote_type: source_master\n"
        "eligible_for_l3: true\nsource_unit_id: report-001\nauthor_id: CN-GEO研究报告\n"
        "source_type: research_report\nsource_path: x.html\nsource_locator: chapter-01\n"
        f"source_unit_sha256: {'a' * 64}\nprimary_theme: '01'\n---\n\n"
        "## 关键发现\n报告总结不能直接成为候选证据。\n\n"
        "## 可引用原文\n> 报告原文支持这个观察。\n\n"
        "## 可直接调用的判断\n派生判断不能直接成为候选证据。\n",
        encoding="utf-8",
    )
    report = read_source_master(path, tmp_path)

    assert locate_source_claim(report, "报告原文支持这个观察。") is not None
    assert locate_source_claim(report, "报告总结不能直接成为候选证据。") is None
    assert locate_source_claim(report, "派生判断不能直接成为候选证据。") is None


def test_locator_requires_the_exact_claim_hash(card):
    candidate = candidate_row(card)
    candidate["source_evidence_locator"] = candidate["source_evidence_locator"].replace(
        "a", "c", 1
    )

    with pytest.raises(CandidateError, match="evidence_not_found"):
        validate_candidate(candidate, card, SEMANTIC_HASH)


def test_complete_numbered_proposition_is_not_a_fragment():
    assert fragment_reasons("第一，企业应先明确客户画像，再投入获客预算。") == ()


def test_semantic_hash_must_match_explicit_manifest_context(card):
    manifest_row = {"id": card.id, "semantic_input_sha256": SEMANTIC_HASH}
    candidate = candidate_row(card)

    validate_candidate(candidate, card, manifest_row["semantic_input_sha256"])

    candidate = candidate_row(card, semantic_input_sha256="c" * 64)

    with pytest.raises(CandidateError, match="semantic_hash_mismatch"):
        validate_candidate(candidate, card, manifest_row["semantic_input_sha256"])


def test_generated_relations_are_never_source_evidence(card):
    assert locate_source_claim(card, "生成关联不能作为证据。") is None


def test_missing_card_audit_is_reported(card):
    with pytest.raises(CandidateError, match="cards_without_disposition:card-002"):
        audit_card_dispositions(
            [{"id": "card-001"}, {"id": "card-002"}],
            [candidate_row(card, source_card_id="card-001")],
        )


def test_unknown_candidate_source_id_is_reported(card):
    with pytest.raises(CandidateError, match="unknown_candidate_source_id:card-999"):
        audit_card_dispositions(
            [{"id": "card-001"}],
            [candidate_row(card, source_card_id="card-999")],
        )


def test_non_entering_dispositions_require_a_reason(card):
    candidate = candidate_row(card, disposition="待人工判断", disposition_reason="")

    with pytest.raises(CandidateError, match="missing_disposition_reason"):
        validate_candidate(candidate, card, SEMANTIC_HASH)
