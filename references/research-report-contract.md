# 研究报告入库契约

## 输入与归档

- 输入为可回溯的单文件 HTML 研究报告。
- 原始 HTML 只读，并以 SHA-256 验证归档副本。
- 原件进入 `04-原始报告归档/<发布方>/<版本>/`，不直接上传 Dify。
- 每个自然章节一张二层卡；章节内部不按固定字数机械切分。

## 兼容身份

现有二层维护程序要求 `blogger`。CN-GEO 报告卡固定使用：

```yaml
blogger: CN-GEO研究报告
source_type: research_report
publisher: CN-GEO
evidence_status: report_observation
```

`blogger` 只用于旧程序的目录和 manifest 兼容，不表示发布方是博主。来源网址使用 `source_url`，数据版本使用 `data_version`，适用样本使用 `sample_scope`。

同一报告拆成多章时，所有卡共享原始文件的 `source_sha256`，并分别保存 `source_locator`（如 `chapter-01`）和章节正文的 `source_fragment_sha256`。报告卡的重复身份键为 `source_sha256 + source_locator`，不能仅凭原始文件哈希把十个章节误判为一张卡。

## 卡片内容

每张卡包含章节用途、关键发现、数据口径、至少三条短原文摘录、可直接调用的判断、可用选题和使用边界。不要添加口播、情绪分析或报告未提供的案例。

## 三层证据

- 数字只有在报告明确给出时保留，并同时带数据版本、分母和样本范围。
- 样本份额不能改写成市场份额；样本观察不能外推为用户规模。
- 观察性或相关性发现不能改写为因果结论。
- 与旧 atom 真同义时补充报告来源；边界或方向不同则分别保留。
- 研究报告来源不参与多人共识计数，使用独立证据类型 `report_observation`。
- 三层继续同步到现有 `geo博主` 唯一文档，不创建新数据集。
