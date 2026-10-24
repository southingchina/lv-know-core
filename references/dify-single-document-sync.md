# Dify 单文档同步规则

## 身份边界

只允许以下精确目标：

- GEO：数据集 `geo博主`，正式文档 `geo博主-三层知识点-全量.txt`
- AI：数据集 `AI博主`，正式文档 `AI博主-三层知识点-全量.txt`

同步前按当前 corpus profile 精确解析数据集；同名或规范化别名超过一个
时停止。所有写请求必须绑定已解析的 dataset ID，禁止对其他知识库写入。

## 同步判断

运行缓存中的 atoms 先生成一个完整投影，每个知识点包含 `ATOM_ID` 与
`CONTENT_HASH`。读取线上正式文档分段并重建 marker map：

- 数据集不存在：只有用户明确允许建库且提供向量模型配置时才创建。
- 数据集存在但正式文档不存在：创建一个正式文档。
- 正式文档唯一且 marker map 完全一致：跳过；同步状态缺失时只恢复状态。
- 正式文档唯一但 marker map 不同：按原 document ID 更新并等待索引完成。
- 存在多个正式文档或其他遗留文档：停止并报告，不自动清理。

更新接口返回原 document ID 是正常原地更新，绝不能把它加入删除列表。V2 不读取旧清理报告的文档数量，也不提供自动删除旧文档的分支。

**一条 atom 对应一个父块**。父块只保留 `ATOM_ID`、`CONTENT_HASH`、
`SOURCE_VAULT`、`SOURCE_CARD_ID`、`SOURCE_PATH`、`EVIDENCE_LOCATOR`、
问题、`CLAIM`、边界和一条有界原文证据。完整来源数组、来源哈希、
`RELATED_ATOM_IDS` 和全部证据保存在完整恢复包；这些审计字段不参与向量检索。
命中父块后必须能按 `SOURCE_CARD_ID`／`SOURCE_PATH` 回到二层母卡。
父块内部以空行分隔元数据、问题、观点、边界和证据，使四个语义字段分别成为
200～300 tokens 以内的子块；不得生成一个与父块内容完全相同的单一子块。
如果只调整投影格式而 atom marker 未变化，必须先生成并验证当前线上版本的
完整恢复包，再显式使用 `--force-projection-update` 原位重建；普通内容同步
不得使用该开关。

## 中断恢复

同步状态依次记录 `prepared`、`indexing`、`completed`。重试时先读取线上实际 marker map：线上已经正确就补写 completed 状态；线上仍旧则继续更新；身份不唯一则停止。

状态和报告只能保存 dataset／document ID、投影哈希、atom marker hash、
数量、阶段和已恢复异常。API Key 只从安全环境或现有凭据容器读取，
禁止输出或写入任何文件。

## 验收

索引完成后重新读取线上正式文档，要求数据集唯一、文档唯一、状态
available/completed、atom 数量及 marker hash 与运行缓存一致。再分别使用
精确原文、纯语义检索和真实口语问题抽检；`ATOM_ID` 全文命中不能替代语义验收。
完成报告导出后删除运行缓存；Dify 是唯一正式三层。
