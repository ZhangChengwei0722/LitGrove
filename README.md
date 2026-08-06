# Research KB Core：可追溯科研知识库核心

[![CI](https://github.com/ZhangChengwei0722/Scientific-Research-Knowledge-Base/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhangChengwei0722/Scientific-Research-Knowledge-Base/actions/workflows/ci.yml)
[![Dependency security](https://github.com/ZhangChengwei0722/Scientific-Research-Knowledge-Base/actions/workflows/dependency-security.yml/badge.svg)](https://github.com/ZhangChengwei0722/Scientific-Research-Knowledge-Base/actions/workflows/dependency-security.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

Research KB Core 是一个跨平台、local-first 的科研知识库确定性执行层。它提供公共
contracts、稳定 ID、结构化存储、provenance、事务、CLI 和 Application Services，
让上层 App、Codex 或 Claude Code 在不破坏证据链和数据边界的前提下处理科研文献。

```text
Research KB App
-> 本地交互界面、任务状态、预览审批、PDF 阅读和运行维护

Research KB Core
-> schema、ID、事务、provenance、确定性流程、Guardian 和可重建投影

Portable Agent Skill + Codex / Claude Code
-> 论文理解、候选生成、跨论文分析和科研综合等语义判断

Private Workspace
-> 用户自己的 PDF、Paper Card、Evidence、Review Memory 和研究记录
```

Core 本身不调用 LLM、不作科研判断，也不包含任何真实论文或私有知识库内容。
这是已经公开的 Core 基础设施仓库，不是完整的 GUI 产品。面向普通用户的产品入口由
`research-kb-app` 承担；在经过脱敏审计的 App 公共仓库实际建立前，此处不提供未经验证的
App 下载或仓库链接。

## 当前状态

P0-P11 路线图已经交付，当前开发 package version 为 `0.1.1.dev2026080603`，公共
Application Service interface 为 `1.19`，workspace layout 为 `p7d-1`。已验证能力覆盖论文导入、解析、Source Adequacy、Primary/Review
语义提交、阅读与 Evidence 回源、发现、研究组织、Research Synthesis、Obsidian
generated views、Exchange、备份恢复和 operational maintenance。

Windows 是当前必需的 live acceptance 平台；路径和数据 contracts 使用 `pathlib`、
UTF-8/LF 与 POSIX relative paths，并保留 host-independent POSIX tests。macOS live
validation 目前是 best-effort，除非后续 milestone 单独要求。

## Core 负责什么

| 能力域 | Core 的职责 |
|---|---|
| Workspace | 配置校验、初始化 marker、layout、domain profile 和 workspace session |
| Source | portable source reference、SHA-256 identity、Source Asset manifestation 和显式 relink |
| Registry | Paper identity、duplicate link、merge/split/alias/archive/tombstone correction |
| Parse | synthetic test adapter、`pdfplumber` 和 `pdfplumber-text-flow` 明确适配器 |
| Source Adequacy | 按具体用途判断解析结果是否支持阅读、引用、图表、公式或补充材料任务，并提供受限的连续正文阅读顺序复核 contract |
| Scientific records | Paper Card、canonical Evidence、review queue、background-only Review Memory 和 revision lineage |
| Agent Task | privacy payload、CAS lease、staging、escaped preview、revision/reject/approve contract |
| Research organization | Direction、Field Map Entry、Question Mapping、Question Screening 和 Tag |
| Knowledge use | Paper/Review reading、Evidence trace、report-only Knowledge Query 和 Research Synthesis |
| Discovery | Europe PMC metadata search、explicit selection、OA resolution 和 create-only acquisition |
| Views and exchange | source-watermarked Markdown、单向 Obsidian sync、rights-aware Exchange |
| Operations | Pipeline Job、process event、Guardian、transaction recovery、backup/restore 和 journal archive |
| Projection | 可删除、可重建的 SQLite/FTS Catalog；永远不是 canonical authority |

## 权责边界

Core 与 Agent 的边界是这个项目最重要的 contract：

- CLI/Application Service 负责确定性 I/O、校验、ID、状态、事务、日志、rendering 和 Guardian。
- Agent 只产生语义候选，不能分配 canonical ID，也不能标记 `human_checked` 或 `verified`。
- Agent 结果必须经过 staging、App preview 和用户批准，才能调用对应 canonical writer。
- Primary 事实性 Card Unit 必须闭合到 canonical Evidence。
- Review Memory 永远是明确标注的 background，不能进入 canonical Evidence。
- Knowledge Query 是 report-only，不自动改写科学记录或 Research Synthesis。
- Research Synthesis 是候选层；内部 `step7-*` 名称仅作为兼容标识保留。
- Markdown、SQLite/FTS 和 Obsidian 页面都是 derived view，不能反向覆盖 structured records。

详细边界见 [docs/architecture.md](docs/architecture.md) 和
[docs/workflow.md](docs/workflow.md)。

## 主流程

### 本地论文

```text
用户选择本地 PDF
-> Workspace / Source confinement
-> Source Asset + Registry
-> Parse
-> use-specific Source Adequacy
-> 用户或 Agent 确认 Primary / Review route
-> 外部 Agent 返回 schema-bound candidate
-> staging + preview + 用户审批
-> canonical bundle commit
-> Guardian
```

Primary bundle 保留 Card Unit、Evidence 和 review boundary 的同一 revision 闭包。
Review bundle 保留同一综述中的 page/section provenance，但始终为 background-only。

### 文献发现与获取

```text
Europe PMC transient search
-> 用户显式选择 metadata candidate
-> zero-write OA resolution
-> 用户显式授权 create-only acquisition
-> local_inbox receipt
-> 停止，不自动 Registry 或 Parse
```

无法自动取得的论文由上层工作流报告题目、DOI 和失败原因，交给用户合法下载。

### 外部 Agent 交接

```text
Core 创建 Agent Task
-> 解析 allowed content classes 与 privacy scope
-> 输出完整 handoff manifest 和 result JSON Schema
-> Codex CLI 或 Claude Code CLI 返回一个 JSON object
-> Core 检查 task/input basis、schema 和 stale state
-> App 转义预览
-> 用户批准、修订或拒绝
```

Core 不启动 Agent、不管理模型凭证，也不允许 Agent payload 中的文本扩大任务权限。

## 快速开始

要求 Python 3.11+。

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,pdf]"
.\.venv\Scripts\python.exe -m research_kb --version
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test,pdf]'
.venv/bin/python -m research_kb --version
```

`pdf` extra 安装真实 PDF 解析所需的受限 `pdfplumber` 版本。没有该 extra 时，
PDF adapter 会明确报告 unavailable，不会静默回退到 synthetic parser。

## 初始化 Workspace

仓库提供两个配置样例：

- [templates/workspace.example.yaml](templates/workspace.example.yaml)
- [templates/domain-profile.example.yaml](templates/domain-profile.example.yaml)

根据自己的私有目录创建配置后，先执行 dry run：

```powershell
research-kb workspace init --workspace <workspace.yaml> --dry-run
research-kb workspace init --workspace <workspace.yaml>
```

初始化只创建批准的 managed scaffold 和 `.research-kb/workspace.json`。它不会扫描或
创建 `local_inbox`，不会修改 source asset，也不会创建 scientific record。

## CLI 导航

使用 `research-kb --help` 查看顶层命令，使用 `research-kb <group> --help` 查看精确
参数和状态。主要命令组如下：

| 命令组 | 用途 |
|---|---|
| `capability` | 查看已实现和当前可用的 adapters/connectors |
| `workspace` / `compatibility` | 初始化 workspace、执行显式 read-only legacy inspection |
| `intake` / `registry` / `identity` | intake preflight、Paper 登记和 identity correction |
| `job` / `source` | Pipeline Job、Source Asset、inbox selection 和 relink |
| `parse` / `adequacy` / `trunk` | 解析、用途级充分性和 deterministic trunk |
| `paper` / `review` | 读取当前 Paper Card、Evidence、queue 或 Review Memory context |
| `record` / `question` | 受校验的 record promotion、Question Mapping 和 reading view |
| `discovery` | Europe PMC search、selection、resolution 和 acquisition |
| `manuscript` | transient DOCX/PDF projection；科研审查逻辑仍由 Skill/Agent 负责 |
| `step7` | Research Synthesis 的内部兼容命令名，仅用于 context/render |
| `backup` / `maintenance` | 备份恢复、journal archive 和 stale maintenance |
| `guardian` / `transaction` | 健康检查和事务恢复 |

常用只读起点：

```powershell
research-kb capability show
research-kb intake inspect --workspace <workspace.yaml> --source <absolute-pdf-path>
research-kb paper status --workspace <workspace.yaml> --paper-id <paper_id>
research-kb guardian check --workspace <workspace.yaml>
```

精确 mutation authority、request schema 和 exit code 以 CLI `--help`、JSON Schema 和
ADR 为准，不以 README 示例替代。

## Portable Agent Skill

仓库内的 [skills/research-kb/](skills/research-kb/) 是受审查的 Skill authoring source，
支持以下任务：

- 本地 Primary/Review PDF intake；
- Europe PMC 检索、用户选择和显式 OA acquisition；
- 单篇/跨论文 Knowledge Query 与 Evidence trace-back；
- Research Synthesis maintenance；
- DOCX/PDF manuscript projection 和按用户标准执行的 transient audit；
- App 创建的 Agent Task 响应。

Python wheel 不安装 Skill。Skill 不复制 schema、ID 或 workflow store，只编排公共 Core
reads/mutations 和外部 Agent 判断。

## Source 与隐私安全

- 仓库禁止真实 PDF、parsed paper text、Evidence quote、Paper Card、研究笔记和凭证。
- Fixture 必须从零编写并标记 `synthetic_from_scratch`。
- 已存在 source asset 默认不可移动、改名、覆盖、编辑或删除。
- 唯一 source-write 例外是用户显式授权的 `discovery acquire` 和
  `copy_into_local_inbox`；二者只能 create one previously absent inbox file。
- Canonical source location 使用 `root_id + POSIX relative_path`，不保存本地绝对路径。
- PDF text、Agent output、Exchange record 和 imported Markdown 均按不可信数据处理。
- External Agent payload 取 Task kind、workspace policy、executor 和当次用户授权的交集。

完整规则见 [docs/privacy-boundary.md](docs/privacy-boundary.md) 和 [AGENTS.md](AGENTS.md)。

## 仓库结构

```text
src/research_kb/       Core services、CLI、contracts 和 adapters
schemas/               JSON Schema Draft 2020-12 公共 contracts
templates/             workspace 与 domain profile 配置样例
agent_protocol/        Agent handoff 公共协议
skills/research-kb/    Portable Skill authoring source
benchmarks/            synthetic scale generators、profiles 和 measurements
tests/                 unit、integration、contract、privacy 和 cross-platform tests
docs/                  architecture、workflow、ADR、plans、receipts 和 closure manifests
```

## 开发与验证

提交前运行：

```powershell
.\.venv\Scripts\python.exe tools/run_validation.py --level L2 --receipt .validation/l2.json
.\.venv\Scripts\python.exe -m build
.\.venv\Scripts\python.exe -m research_kb --version
.\.venv\Scripts\python.exe -m research_kb privacy scan --root .
```

修改 schema、状态、路径、ID、workspace layout 或 write authority 时，必须先有明确的
设计/计划，再执行 targeted tests、完整 L3 shards、L4 scale、build、privacy scan 和
diff review。验证层级、分片和 receipt 规则见 [Test Validation](docs/test-validation.md)。

## 文档索引

- [架构](docs/architecture.md)
- [工作流](docs/workflow.md)
- [贡献指南](docs/contributor-guide.md)
- [隐私边界](docs/privacy-boundary.md)
- [架构决策记录](docs/decisions/)
- [P11 operational acceptance](docs/p11-operational-acceptance-closure-manifest.md)

## 贡献、支持与发布

- 提交 issue 或 pull request 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和
  [Contributor Guide](docs/contributor-guide.md)。
- 使用问题与维护边界见 [SUPPORT.md](SUPPORT.md)；疑似漏洞或凭证泄露必须按
  [SECURITY.md](SECURITY.md) 私密报告，不要创建公开 issue。
- 版本、兼容性、弃用和制品要求见 [Release Policy](docs/release-policy.md) 与
  [CHANGELOG.md](CHANGELOG.md)。当前没有已发布的稳定版本或自动 PyPI 发布。

## 许可证

本项目使用 [Apache License 2.0](LICENSE)。提交贡献即表示同意按相同许可证提供该贡献。

## 当前未包含

- Core 内置 LLM 或 embedded Agent runtime；
- OCR、自动版面修复或把 parser 输出当作 layout verification；
- 第二个 discovery provider、机构账号或浏览器会话 acquisition；
- subtype-specific Review schema 和 Review Unit factual Question Mapping；
- arbitrary Markdown import、Obsidian reverse sync 或 unregistered custom views；
- external Exchange record 自动提升为本地 canonical record；
- 私有工作区 migration、legacy write freeze 或 cutover。

下一道产品门是 source-free `private_workspace_pilot_design`；只有随后获批的精确执行
manifest 才能授权具体私有路径和案例。P0-P11 关闭不自动授权访问或迁移任何私有科研数据。
