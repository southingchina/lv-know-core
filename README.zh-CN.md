# LV-Know Core · 可追溯知识库加工框架

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md)

从业务知识库流程中提取的 Python 核心：来源母卡、稳定 ID、结构化字段、二层卡片、候选知识点、增量依赖、阶段校验和带证据的最终验收。目标是让每次加工能追踪输入与产物，区分内容数量和实际质量。

## 适用场景

- 将已有合法来源整理为有 schema、稳定身份和来源哈希的卡片。
- 增量更新时定位受影响来源、候选与下游产物。
- 在进入下一阶段前检查缺失字段、证据引用、数量和结构约束。
- 为不同领域配置 profile；按需把明确选定的文档同步到 Dify。

本项目不是爬虫、自动写稿服务或“万能去重产品”。语义灰区仍需人工或外部模型判断；确定性校验通过不等于知识判断正确。

## 安装与先验证

Python 3.10+，核心主要使用标准库；测试需要 pytest：

```shell
git clone https://github.com/southingchina/lv-know-core.git
cd lv-know-core
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
python scripts/profile_config.py --help
python scripts/layer2_cards.py --help
python scripts/stage_gate.py --help
```

先运行临时目录中的合成测试，了解输入输出，再对自己的资料执行实际操作。各 CLI 参数以对应 `--help` 为准，详细字段见 `references/`。

## 配置和工程结构

`profiles/` 包含 GEO、AI、电商等领域的结构示例，库目录统一为 `./example-vault/<profile>`，远端数据集/文档 ID 留空。请替换成自己的目录和明确目标，不能直接当作已配置的线上项目。

`scripts/` 包含核心合同、选择范围、生成、校验、增量与同步模块；`assets/` 是空白模板；`tests/` 使用合成数据。Dify 默认示例地址为回环 `http://127.0.0.1:5001/v1`，使用前检查其配置和权限；运行普通测试不会同步真实服务。

## 公开版范围与限制

原知识库正文、客户材料、作者原作、真实处理产物和内网配置不分发。原业务专用 Codex Skill 未发布；依赖宿主私有 Artifact SDK 的交互审阅器与安装环境绑定测试也未发布，清单见 `PUBLIC-SCOPE.json`。公开版提供 JSON/Markdown 核心和测试，不宣称具备独立 Excel 审核界面。

Dify 同步、模型辅助审核等外部集成需要使用者单独配置和验证。不要把某次私有环境的历史验收数字当作公开版当前测试结果；以本仓库实际测试为准。MIT。
