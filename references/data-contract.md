# 三层数据契约

## 运行期三层缓存

```text
<run_dir>/layer3-cache/preview/
├── scope.json
├── candidates.jsonl
├── atoms.jsonl
├── atom-index.jsonl
├── question-registry.json
├── source-map.json
├── build-metrics.json
├── run-state.json
├── validation.json
├── manual-approval-delta.json   # 有人工审核时存在
└── manual-approval-delta.json   # 有人工审核时存在
```

`atoms.jsonl` 是本次生成 Dify 正式单文档的临时 canonical 输入。Dify
是唯一正式三层；上述文件在 Dify 验收和报告导出后全部删除，不进入
Obsidian，也不保留为长期本地镜像。

## scope.json

必填：`schema_version`、`manifest_sha256`、`mode`、`count`、`cards`。每张卡包含 `id`、`title`、`blogger`、`primary_theme`、`related_themes`、`relative_path`、`sha256` 和 `selection_reason`。正式写入和最终验收都重新检查 manifest 与来源卡哈希。

## candidates.jsonl

每行一个候选观点，至少包含：

- `candidate_id`、`candidate_hash`
- `answerable_question`、`question_keys`
- `source_claim`（可选）、`claim`、`type`
- `primary_theme`、`related_themes`、`evidence_status`、`boundary`
- `source_card_id`、`source_blogger`、`source_card_sha256`
- `source_type`；研究报告候选另外保存 `source_data_version` 与 `source_sample_scope`
- `disposition`、`disposition_reason`、`transformation_type`

只有 `进入三层` 的候选可以被 atom 引用。待判断和仅保留二层候选必须保留，不能静默删除。

## atoms.jsonl

每行一个 canonical 知识点，至少包含：

- `atom_id`、`answerable_question`、`question_keys`、`claim`、`type`
- `knowledge_status`、`primary_theme`、`related_themes`、`boundary`
- `source_card_ids`、`source_candidate_ids`、`related_atom_ids`
- `source_vault`、`source_relative_paths`、`source_card_sha256s`
- `evidence_quotes`、`evidence_locators`
- `version`、`content_hash`
- 替代旧知识时保存 `replacement_atom_ids` 与 `retired_reason`

新增来源不改变已有 `atom_id`；同义合并保留更早主 ID；拆分时原 ID 留给原核心命题。
失效或被替代的 atom 退出活跃 Dify 投影，但连同替代关系、原因和原始证据
进入写入前的压缩恢复包与最终审计 receipt。

## 三个同步索引

- `atom-index.jsonl`：每个 atom 恰好一行，至少保存 atom ID、问题键、主题、状态和内容哈希。
- `question-registry.json`：保存稳定问题键和同义别名；atom 使用的问题键必须已登记。
- `source-map.json`：`card_to_atoms` 与 `atom_to_cards` 必须与 atoms 双向一致。

主题直接保存在 atom index，不另建重复主题索引。

## build-metrics.json

指标只能从最终 scope、candidates、atoms、三个索引与本次下载的 Dify
基线重算。至少包含：

- `generated_from`：本次最终输入文件 SHA-256；
- `comparison_from`：上一版 atoms SHA-256；
- `counts`：全库 atom、候选三类分流、状态、索引、问题键、来源卡、本批范围与本批候选；
- `changes`：新增 atom、合并进已有 atom、更新已有 atom、未变化 atom、
  退出活跃检索 atom、替代关系。

验证器重新计算后必须完全相等。禁止复制旧报告或旧 `build-metrics.json` 的数字。

## 运行状态

`content-review.json` 按 [来源忠实性规则](source-fidelity.md) 保存实际内容检查记录，
绑定当前比较哈希、活跃预览文件SHA及每个活跃atom的ID/内容哈希。
`stage_gate.py record-content-review` 核对覆盖与版本，`check-layer3-publish`
复查这些文件未变；没有此记录的旧运行不得仅凭比较完成就写回。
该程序校验记录完整性，不替代自然语言语义判断。审核记录随运行缓存归档/清理，
不得变成独立的长期三层库。

运行期间保存策略版本、最近成功处理的卡 ID／哈希／路径，以及所有
atom 的版本和内容哈希。恢复运行时可以复用；验收完成后仅把必要哈希、
数量和 Dify ID 摘入最终 receipt，详细运行状态随缓存删除。

## 人工审核差异

`manual-approval-delta.json` 保存审核工作簿 SHA-256、批准、修改、继续待
判断和零变化 candidate ID。最终报告只保留统计和哈希；工作簿及逐条
临时差异在 Dify 验收后随缓存删除。

## run-summary.json

最终验收前由本次运行生成，至少包含 `input_transcripts`、`skipped_sources`、`staged_layer2_cards`、`committed_layer2_cards` 和 `layer2_index_changes` 五个非负整数。`final_acceptance.py --run-summary` 将这些二层数据与三层指标、Dify 状态合入唯一最终报告。

## Dify 备份、写入与清理

写入前把当前 Dify 正式文档、marker map、检索配置及精确 dataset/document
ID 打成压缩恢复包，保存到专用备份目录，并验证压缩包可打开、内容哈希
与下载基线一致。不得把解压内容保留为活动镜像。

最终验收后，先把 `final-acceptance.json`、`review-report.md` 和最小化
`cleanup-receipt.json` 导出到运行输出目录，再删除完整
`<run_dir>\layer3-cache`。receipt 只保留哈希、计数、精确 Dify ID、
验收状态和清理证据。

## Dify 单文档投影

运行期 atoms 按 atom 增量处理，Dify 始终投影为一个正式文档。投影为
每个活跃 atom 只生成一个紧凑父块，保留 `ATOM_ID`、`CONTENT_HASH`、
`source_vault`、主 `source_card_id`、主 `source_relative_path`、主
`evidence_locator`、问题、观点、边界和一条有界原文证据。完整
`source_card_sha256s`、`evidence_quotes`、`question_keys`、
`related_atom_ids` 和全部来源映射保存在压缩恢复包，不参与向量检索。
同步程序比较完整 marker map 和投影哈希：未变化跳过，变化更新现有文档，
线上已经正确则恢复状态。不得把每个变化 atom 单独生成线上文档。
