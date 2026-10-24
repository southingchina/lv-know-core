# 二层素材卡契约

## 角色

二层保存可回溯、可直接用于内容创作的素材，不是去重后的观点表，也不是三层 atom。不同博主对同一观点的案例、表达、语气和口播结构都可以并存。

## 全量接收

- 每个可读取的用户输入单元都必须进入暂存记账。
- 内容短、观点少、没有三条引句或不易提炼，不是二层跳过条件。
- 二层不判断观点是否正确，也不负责发布审核。
- 同一来源单元的幂等重跑映射到既有卡；不同来源即使正文相同也分别保留。

无法读取的输入必须记录明确错误原因，不得静默跳过。`unknown` 类型也必须保留完整原稿并进入二层。

## 原稿保存

- 所有二层来源主卡都必须保留该单元完整原始内容。
- 原始内容只统一 BOM 和换行，不删除语气词、重复句、停顿，不调整句子顺序。
- `short_video`、`long_form` 和转录型 `unknown` 来源必须使用强制的 `<summary>完整原始口播稿</summary>` 块，内含完整逐字原稿。
- `article_or_interview` 来源也必须保留完整文章或访谈原文；仅展示标签可随来源类型变化，不能以摘要、段落视图或派生章节代替原文。
- 研究报告继续遵循 [研究报告契约](research-report-contract.md) 的既有 HTML route，不改其路线，但也必须保留完整来源正文。
- 校对版是可选派生视图，不能覆盖完整原始内容。
- 短视频一个输入单元一张主卡；长内容每节保留完整本节原稿，并使用组级上下文。

## 卡片身份

每张卡必须有稳定 `id`、唯一标题、`source_path`、`source_sha256`、一个且仅一个 `primary_theme`、来自正式标签表的 `tags`，以及完整原始内容。`related_themes` 可以为空。

v2 frontmatter 固定增加以下字段：

```yaml
note_type: source_master
eligible_for_l3: true
source_unit_id: 博主-source-001
source_type: short_video
source_locator: whole-file
source_unit_sha256: "64位小写SHA-256"
idempotency_key: "四字段身份的64位小写SHA-256"
extraction_version: "lv-know-l2-v2"
enrichment_status: complete
```

`source_type` 可为 `short_video`、`long_form`、`article_or_interview`、`research_report` 或 `unknown`。`source_locator` 标识该逻辑单元在来源中的位置；`source_unit_sha256` 是仅统一编码和换行后的逻辑单元哈希。`enrichment_status` 只能描述派生信息是否完整（`complete` 或 `partial`），不会影响二层入库。

幂等键为规范化 `source_path + source_sha256 + source_locator + source_unit_sha256`。只有完整四字段身份完全一致时才映射到正式 manifest 的既有卡并 no-op；不同来源路径即使正文相同、同一文件的不同 locator 即使共享文件哈希，也都不合并。旧 manifest 行若没有合法 `idempotency_key`，只有四字段齐全时才可推导；字段不全的旧行仍可读取但永不作为 exact identity。

## 可选派生信息

只输出原稿实际支持的栏目，例如内容主旨、关键观点、具体案例、方法步骤、条件限制、表达结构、短视频钩子和收口、长内容上下文位置、跨段关联、可复用原话或校对版。不得输出空栏目，不得把所有观点包装为“作者认为”。

短视频钩子若存在，必须是原稿开头的连续原文，不得用后文金句替代。长内容的组级上下文、上一节、当前节和下一节必须通过 `context-packets.jsonl` 提供给提炼器；它们用于避免局部误解，不能替代本节完整原稿。

分组 `long_form` 的每个单元必须提供正整数 `order`，且同组不可重复；完整上下文包的组成员、顺序、总览、上一/当前/下一单元、相邻摘要和下一单元 raw path 必须与 inventory 和 context map 一致。未分组长内容可以保持 `partial`，但 partial 包只能保留 current，不得携带 global、previous 或 next 造成伪上下文。

## 暂存布局

```text
work/lv-know/<run_id>/
├── source-inventory.json
├── context-map.json
├── context-packets.jsonl
├── extraction-results.jsonl
├── staged-layer2-manifest.json
├── layer2-cards/
└── reports/
```

`staged-layer2-manifest.json` 必须是对象并包含 `cards` 数组。暂存 manifest 同时在 `run-state.json` 中绑定正式二层 manifest 的基线哈希；暂存阶段不得修改正式 manifest。

## 正式写入边界

本契约不授权正式写入。正式二层提交只经过自己的 `stage_gate.py check-layer2-commit`
安全门：二层验证已通过、可恢复快照存在且哈希正确、正式二层 manifest 基线未变化。
三层候选对比与 Dify 基线属于之后的 `check-layer3-publish`，不得反向阻塞二层提交。
两段都沿用唯一写入者与事务性提交。

研究报告不套用短视频钩子或口播结构。

通用转录稿 renderer 不接收 `research_report`；HTML 报告必须绕过通用 inventory/context/extraction/render 命令，继续使用既有 [研究报告契约](research-report-contract.md) 与研究报告卡模板。
