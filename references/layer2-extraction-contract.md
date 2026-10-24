# Layer-2 v2 提炼输出契约

以下 JSON 是 v2 的规范形状。盘点结果、长内容组地图和单元提炼结果分别独立存储；字段可为空时应保持其 JSON 类型。

## 来源单元盘点

```json
{
  "schema_version": 2,
  "units": [
    {
      "unit_id": "author-001",
      "card_id": "author-001",
      "blogger": "author",
      "source_title": "title",
      "path": "D:/input/title.md",
      "locator": "whole-file",
      "source_type": "short_video",
      "primary_theme": "02",
      "group_id": null,
      "order": null,
      "start_line": null,
      "end_line": null
    }
  ]
}
```

实际 `source-inventory.json` 保留 `start_line`、`end_line`，并在读取来源后增加 `source_path`、文件/单元 SHA-256、四字段 `idempotency_key` 与 `raw_text_path`。正式 manifest 只按合法 `idempotency_key` 或齐全四字段建立 exact identity；仅有旧文件哈希的行不能触发 no-op。

## 长内容组地图

```json
{
  "schema_version": 2,
  "groups": [
    {
      "group_id": "course-a",
      "overview": "课程整体解决什么问题",
      "glossary": [{"term": "GEO", "meaning": "本课程中的定义"}],
      "units": [
        {"unit_id": "lesson-01", "summary": "第一节的作用"}
      ]
    }
  ]
}
```

## 长内容上下文包

`context-packets.jsonl` 对每个输入单元写入一条记录。下面是长内容中间课时的规范记录；字段及嵌套形状必须与 `build_context_packets` 的输出完全一致：

```json
{
  "unit_id": "lesson-02",
  "source_type": "long_form",
  "mode": "long_form_context",
  "context_status": "complete",
  "global_context": {
    "overview": "三节课的整体路线",
    "glossary": [{"term": "GEO", "meaning": "AI 搜索优化"}]
  },
  "previous": {
    "unit_id": "lesson-01",
    "summary": "第一节"
  },
  "current": {
    "unit_id": "lesson-02",
    "raw_text_path": "D:/input/course-a/lesson-02.md"
  },
  "next": {
    "unit_id": "lesson-03",
    "summary": "第三节",
    "raw_text_path": "D:/input/course-a/lesson-03.md"
  }
}
```

`current` 始终包含当前单元的 `unit_id` 与 `raw_text_path`；长内容的 `mode` 为 `long_form_context`。首节的 `previous`、末节的 `next` 可为 `null`。缺少或无法匹配组地图时，仍输出当前单元并将 `context_status` 设为 `partial`；完整组地图存在时为 `complete`。提炼器必须消费 `global_context`、`previous`、`current` 和 `next`，但不得让上下文替代 `current.raw_text_path` 所指向的完整原稿。

分组长内容的 `order` 必须为正整数且组内唯一。`complete` 包必须按 inventory 顺序与 context map 逐字段匹配相邻单元、摘要、下一单元 `raw_text_path` 和全局总览；`partial` 包的 `global_context`、`previous`、`next` 必须全部为 `null`。

## 单元提炼结果

```json
{
  "unit_id": "author-001",
  "card_title": "清晰标题",
  "additional_tags": [],
  "enrichment_status": "complete",
  "hook": {
    "quote": "必须是原稿开头的连续原文",
    "function": "说明它如何建立冲突或悬念"
  },
  "sections": [
    {
      "heading": "内容主旨",
      "paragraphs": ["按内容实际需要生成"],
      "items": []
    }
  ],
  "calibrated_transcript": null
}
```

`hook`、`sections` 和 `calibrated_transcript` 都是可选字段：提炼器无法可靠产出时可缺省或为 `null`，渲染器不得生成空栏目。`primary_theme` 来自 source spec，因此即使语义提炼失败，最小原始来源卡仍可渲染；此时 `enrichment_status` 为 `partial`。

字段一旦出现就必须有实质内容：空白 section heading、空白 hook quote/function、空白列表项，以及空白 item label/text 都是输入错误，渲染器必须在发布任何暂存卡前拒绝。

## 原话与分析边界

- `hook.quote` 只放原稿开场的连续原话；`hook.function` 才解释吸引机制。
  第三人称摘要、改写和“校正后”的新句子不能写进原话字段。无法确定时留待核，不用固定
  前几秒填满字段，也不从空引文推断视频没有钩子。
- 校正须保留原始形式与依据。不能仅因专名陌生，就断为ASR错误或猜成另一个产品。
- `complete` 只描述加工完成。证据可访问、原句定位、语义复核分别记账。
- 文章来源的渲染标签为“完整原始正文”；转录来源仍用“完整原始口播稿”。
  图文的说明、OCR、视觉资产各标来源，不能把OCR全文当成已看过全部图片。
- 来源路径失效、历史统计、类型标签与命题真假分别处理；未知数据保留未知。
