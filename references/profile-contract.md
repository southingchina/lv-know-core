# lv-know knowledge-type profile contract

每个知识类型使用 `profiles/<profile_id>.json` 注册。它只声明内容差异，不执行代码，
也不能改变公共状态机。

必填字段：

- `profile_id`、`display_name`
- `obsidian_vault_root`
- `routes`：`source_type -> 二层相对目录`，可含 `default`
- `dify_dataset`：精确 ID 或名称
- `target_documents[]`：精确目标及声明式 `match_rules`；必须且只能有一个 default
- `templates.layer2`、`templates.layer3`
- `taxonomy`：基础标签、主题映射或格式、附加标签白名单、文件名兜底
- `domain_rules[]`：可为空，只能引用静态规则文件

同步模式不在 profile 中。它是 run-level 参数，默认 `segment_incremental`，可显式选择
`full_document_replace`。禁止 profile 自定义 hook、Python 类、动态模块或回调函数。

新增类型的验收标准：新增配置、模板/规则和测试后，不修改公共 Python 代码，也能完成
配置校验、二层路由、目标文档路由以及两种同步模式的离线契约测试。
