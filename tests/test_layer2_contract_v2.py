import pytest
import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def json_blocks(document: str) -> list[dict]:
    return [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", document, flags=re.DOTALL)
    ]


def section_body(document: str, heading: str) -> str:
    match = re.search(
        rf"^{re.escape(heading)}\n\n(.*?)(?=^#{{1,{heading.count('#')}}} |\Z)",
        document,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match, f"missing section: {heading}"
    return match.group(1).strip()


def test_layer2_contract_has_no_content_admission_gate() -> None:
    contract = read("references/layer2-contract.md")

    assert "不足 80 个有效字符" not in contract
    assert "找不到三条有效原文引句时，不硬凑卡片" not in contract
    assert "每个可读取的用户输入单元" in contract
    assert "不判断观点是否正确" in contract


def test_template_requires_complete_raw_transcript_and_v2_identity() -> None:
    template = read("assets/layer2-card-template.md")

    for field in (
        "note_type: source_master",
        "eligible_for_l3: true",
        "source_unit_id:",
        "source_type:",
        "source_locator:",
        "source_unit_sha256:",
        "idempotency_key:",
        "extraction_version:",
        "enrichment_status:",
    ):
        assert field in template
    assert "<summary>完整原始口播稿</summary>" in template
    assert 'data-lv-know="raw-transcript"' in template
    assert "<summary>原博主校对版口播稿</summary>" not in template


def test_layer2_contract_uses_its_own_commit_gate_not_layer3_comparison() -> None:
    contract = read("references/layer2-contract.md")

    assert "check-layer2-commit" in contract
    formal_boundary = section_body(contract, "## 正式写入边界")
    assert "二层验证" in formal_boundary
    assert "可恢复快照" in formal_boundary
    assert "正式二层 manifest 基线" in formal_boundary
    assert "不得反向阻塞二层提交" in formal_boundary


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_v2_examples_cover_short_and_long_routes() -> None:
    short = read("references/approved-short-video-example.md")
    long_form = read("references/approved-long-form-example.md")

    assert "source_type: short_video" in short
    assert "note_type: source_master" in short
    assert "eligible_for_l3: true" in short
    assert "source_unit_id:" in short
    assert "idempotency_key:" in short
    assert "### 开场钩子" in short
    assert "<summary>完整原始口播稿</summary>" in short
    assert "source_type: long_form" in long_form
    assert "note_type: source_master" in long_form
    assert "eligible_for_l3: true" in long_form
    assert "source_unit_id:" in long_form
    assert "idempotency_key:" in long_form
    assert "## 上下文位置" in long_form
    assert "<summary>完整原始口播稿</summary>" in long_form


def test_extraction_contract_defines_task3_compatible_context_packet() -> None:
    contract = read("references/layer2-extraction-contract.md")
    packets = [block for block in json_blocks(contract) if block.get("mode") == "long_form_context"]

    assert len(packets) == 1
    packet = packets[0]
    assert set(packet) == {
        "unit_id",
        "source_type",
        "mode",
        "context_status",
        "global_context",
        "previous",
        "current",
        "next",
    }
    assert packet["source_type"] == "long_form"
    assert packet["context_status"] == "complete"
    assert set(packet["global_context"]) == {"overview", "glossary"}
    assert set(packet["previous"]) == {"unit_id", "summary"}
    assert set(packet["current"]) == {"unit_id", "raw_text_path"}
    assert set(packet["next"]) == {"unit_id", "summary", "raw_text_path"}


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_long_form_example_is_middle_lesson_with_full_context() -> None:
    long_form = read("references/approved-long-form-example.md")

    assert "id: GEO课程-002" in long_form
    assert 'source_locator: "course-a/lesson-02"' in long_form
    for label in ("全局语境", "上一节", "当前节", "下一节"):
        assert label in section_body(long_form, "## 上下文位置")


def test_contract_preserves_complete_source_content_for_every_route() -> None:
    contract = read("references/layer2-contract.md")

    assert "所有二层来源主卡都必须保留该单元完整原始内容" in contract
    identity = section_body(contract, "## 卡片身份")
    assert "以及完整原始内容" in identity
    assert "以及完整原始口播稿" not in identity
    assert "article_or_interview" in contract
    assert "<summary>完整原始口播稿</summary>" in contract
    assert "研究报告继续遵循 [研究报告契约]" in contract
    assert "完整来源正文" in contract


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_short_example_hook_is_raw_prefix_and_has_no_empty_optional_sections() -> None:
    short = read("references/approved-short-video-example.md")
    hook = re.search(r"^> (.+)$", section_body(short, "### 开场钩子"), flags=re.MULTILINE)
    raw = re.search(
        r'<pre data-lv-know="raw-transcript">(.*?)</pre>', short, flags=re.DOTALL
    )

    assert hook
    assert raw
    assert raw.group(1).startswith(hook.group(1))
    for heading in ("## 内容主旨", "### 开场钩子", "## 推进与收口"):
        assert section_body(short, heading)


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_skill_routes_sources_before_layer2_rendering() -> None:
    skill = read("SKILL.md")

    required = (
        "source-spec.json",
        "source-inventory.json",
        "context-map.json",
        "context-packets.jsonl",
        "extraction-results.jsonl",
        "validate_layer2_stage.py",
        "短视频",
        "长课程",
        "完整原始口播稿",
    )
    for marker in required:
        assert marker in skill
    assert "不判断观点是否正确" in skill
    assert "来源不足 80 个有效字符" not in skill
    assert "找不到三条有效原文引句时，不硬凑卡片" not in skill


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_skill_uses_full_identity_manifest_mapping_without_sha_only_skip() -> None:
    skill = read("SKILL.md")

    assert "同一来源哈希已在二层 manifest 中存在时" not in skill
    assert "--existing-manifest" in skill
    assert "idempotency_key" in skill
    assert "完整四字段身份" in skill


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_skill_routes_research_reports_around_generic_transcript_commands() -> None:
    skill = read("SKILL.md")

    assert "research_report 绕过通用转录稿命令" in skill
    assert "research-report-contract.md" in skill
    assert "research-report-card-template.md" in skill


UPDATER = Path(r"./example-vault\GEO内容弹药库\03-维护与模板\Update-GeoKnowledgeBase.ps1")


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_formal_updater_embeds_v2_template_without_running() -> None:
    updater = UPDATER.read_text(encoding="utf-8-sig")

    for marker in (
        "source_type:",
        "source_locator:",
        "source_unit_sha256:",
        "idempotency_key:",
        "extraction_version:",
        "enrichment_status:",
        "<summary>完整原始口播稿</summary>",
    ):
        assert marker in updater
    assert "少于 80" not in updater
    assert "三条有效原文" not in updater


@pytest.mark.skip(reason="Private Skill, author examples or live-vault updater are outside this public core; see PUBLIC-SCOPE.json")
def test_formal_updater_keeps_card_path_separate_from_source_metadata() -> None:
    updater = UPDATER.read_text(encoding="utf-8-sig")

    assert "card_file_path = $File.FullName" in updater
    assert "source_path   = [string]$Metadata['source_path']" in updater
    assert "$Note.card_file_path" in updater
    projection = re.search(
        r"foreach \(\$OptionalKey in @\((.*?)\)\)", updater, flags=re.DOTALL
    )
    assert projection
    for field in ("idempotency_key", "source_path", "source_sha256"):
        assert f"'{field}'" in projection.group(1)
