# PaperTeam：多智能体论文阅读与原仓库实验

基于 **LangGraph** 的六角色工作流：阅读规划、方法分析、实验分析、证据核验、实验复现、报告综合。阅读结论附 PDF 页码和原文证据；复现从每篇论文出发，**搜索并核对 GitHub，下载源码，依据原仓库配置、运行和评估实验**。

## 快速开始

需要 Python 3.12；执行第三方仓库还需要已启动的 Linux Docker 引擎。

```powershell
git clone https://github.com/LinksLid/Agent-for-Paper-Reading.git
cd Agent-for-Paper-Reading
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
```

将 PDF 放入 `paper/`，按 `.env.example` 在根目录配置备用接口：

```dotenv
Base_url=https://your-provider.example/v1
API_KEY=your-key
MODEL=
MAX_API_CALLS=80
API_TIMEOUT=120
```

同时兼容 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 和 `OPENAI_MODEL`。论文片段和公开仓库材料会发送到该 API，请使用获准处理这些材料的服务。可按 `deepseek.env.example` 配置 `deepseek.env`，优先使用 DeepSeek，预算不足自动切到 `.env` 的同济接口。真实配置不提交 GitHub。

## 使用

界面默认启用「查找 GitHub 并尝试论文实验」。旧阅读结果可在 **论文实验复现** 页签单独启动，无需重做阅读分析。逐篇展示仓库、提交、计划、命令、产物、实测指标或具体缺口。

```powershell
# 仅本地解析 PDF，不调用 API
.venv/Scripts/python.exe -m paperteam index
# 模型列表；--provider tongji 可显式使用备用接口
.venv/Scripts/python.exe -m paperteam models
# 阅读和原仓库实验
.venv/Scripts/python.exe -m paperteam read --paper MAC-SQL --paper CHESS --reproduce --samples 2
# 单独执行实验流程，不重复阅读
.venv/Scripts/python.exe -m paperteam reproduce --paper ReAct --samples 2
# 处理 paper 中的每篇论文
.venv/Scripts/python.exe -m paperteam reproduce --samples 2
```

`--samples` 是目标样本数，仅在原实验入口可控制样本量时生效。不会生成玩具数据凑样本。

## 可选：让复现 Agent 调用 Codex

在界面「复现实验规划与修复」选择 **Codex CLI**，或使用：

```powershell
# 首次在本机安装官方 Codex CLI 并登录；已有登录可直接复用
codex login
codex login status
.venv/Scripts/python.exe -m paperteam reproduce --paper ReAct --samples 2 --reproduction-backend codex
# 阅读工作流中也可启用
.venv/Scripts/python.exe -m paperteam read --paper ReAct --reproduce --reproduction-backend codex
```

LangGraph 仍负责角色、状态、分支和返工；Codex 是复现 Agent 的代码工具，接管「制定实验计划」与「按真实错误修复计划」两个节点。搜索核对、论文阅读、结果解释和原实验中的模型请求仍使用现有 API。实验命令与兼容补丁先通过原文依据和路径校验，再交给 Docker 执行；Codex 无法直接在宿主机运行论文代码。

通过官方 `codex exec` 的非交互模式、JSON Schema 输出和只读沙箱接入。仅向它提供已检查的仓库片段与必要日志，不传入 `.env` 内容；禁用此次调用的命令工具、插件、子任务、搜索和用户配置加载。认证复用 CLI 自己的缓存登录，模型使用忽略用户配置后的 CLI 默认值。当前支持原生 CLI，包括官方 npm 的 Windows 安装布局。需要支持 `--ignore-user-config`、`--ephemeral`、`--output-schema` 的 CLI 版本；本机检测到的原生 CLI 为 0.164.0-alpha.11.1，npm CLI 为 0.147.0，运行时优先原生程序。

**Codex 用量不属于 DeepSeek 20元预算。** ChatGPT 登录使用对应 Codex 账户额度；API 凭据登录按该账户规则计费。每次流程最多 10 次 Codex 调用，每次超时 300 秒，初始计划与最多一次修复分别计数；`--max-calls` 仍仅约束现有 API。Codex 不可用、超时或结构校验失败时记录失败，不静默切换后端。用量元数据保存在实验输出的 `codex/codex_usage.jsonl`，结果中分别记录 `planning_backend`、`codex_calls` 和原实验实际使用的模型。

接入 Codex 可以辅助代码分析和依赖兼容，不能补足未提供的数据、权重、GPU，也不代表获得论文原模型或完整复现指标。默认仍使用现有 API；新增开关不会自动启动实验。接入测试使用模拟 CLI 和容器，不把测试夹具结果当真实实验。

接口依据：[OpenAI Docs：非交互运行](https://learn.chatgpt.com/docs/non-interactive-mode)、[认证与账户用量](https://learn.chatgpt.com/docs/auth)。

## 工作流

阅读：PDF 按页索引 → 规划 → 方法/实验并行分析 → 原文定位与语义核验 → 定向补查 → 逐篇实验 → 报告与比较结论核验。

实验是独立 LangGraph 子图：**搜索 → README 对应关系核验 → 下载固定提交 → 检查仓库 → 制定原实验计划 → 容器执行 → 核对产物**。命令失败后，根据真实日志修复最多一次，再尝试执行。

既检查论文内的链接，也按准确标题、方法名和作者搜索；README 必须提供对应关系的原文，排除被引用的依赖库和无关同名项目。搜索失败不会被解释为论文没有代码。搜索和源码缓存复用一天，复用源码时核对文件哈希。GitHub 公共 REST 额度不足时，可用只读 Git ref 查询获取公开仓库提交。

## 如何运行原实验

使用原仓库脚本或 Notebook、真实数据与评估逻辑。允许记录清楚的依赖版本、API、路径、样本上限和结果导出兼容补丁；不改算法、数据标签、金标准或评分函数来制造成功。Notebook 直接执行修改后的代码单元，保留输出。

Docker 使用只读根目录、2 CPU、4GB 内存、进程数限制和最小权限。只挂载对应源码（只读）和本次输出目录；主项目、PDF、两份真实配置和 Docker socket 均不挂载。容器结束后删除，源码和日志保留。Docker 不可用时保留计划和原因，不回退到无操作系统隔离的本机执行。

原代码只获得临时 API 代理 token，真实密钥保留在主进程；代理继续执行累计20元预算和调用次数限制。支持非流式文本 chat/completions、旧 completions 与 Azure 风格 chat 路径；暂不支持 embedding、视觉、工具调用和流式。容器未配置 GPU，不自动下载大型模型权重。缺数据、权重或其他服务时，逐项列出名称和路径。

| 状态 | 含义 |
|---|---|
| completed | 原实验运行并产生实际产物；基准与评估任务还需有可解析指标 |
| executed | 已运行，但产物或指标不足以确认完成 |
| smoke_passed | 仅环境、帮助命令或单元测试通过 |
| blocked | 缺少明确的运行前提 |
| failed | 某阶段实际失败，保留日志与原因 |
| not_found | 本轮未核实到对应仓库，不代表全球没有实现 |

源码已下载与实验已完成分别显示。不同模型、小规模子集或兼容补丁产生的结果不自动宣称复现论文完整指标。旧版 MAC-SQL 合成数据库验证代码和示例仅作历史记录，**已不是当前论文复现流程**。

## 结果文件

阅读结果在 `output/<run-id>/`：`report.md`、`state.json`、`manifest.json`、SQLite 检查点、API 用量与实际编排图。实验按论文 ID 单独保存：

| 文件 | 内容 |
|---|---|
| discovery.json | 搜索、候选、README 对应依据 |
| source.json | 仓库、提交、压缩包与文件哈希 |
| inspection.json | 实验入口、依赖、数据等检查 |
| plan.json / repaired_plan.json | 初始计划与日志驱动的修复计划 |
| progress.json / result.json | 当前阶段与最终结果 |
| commands.json / execution_history.json | 真实命令、退出码、输出和耗时 |
| patches/ | 兼容性修改后的副本，原下载源码不变 |
| artifacts/ | 原实验产生的结果与已执行 Notebook |

指标只从实际 JSON 产物解析数值，不接受 LLM 估算。密钥、论文、源码下载、依赖环境和运行缓存均不提交。

## DeepSeek 20元预算

存在 `deepseek.env` 时优先用 deepseek-flash。每次付费调用前查人民币余额，按输入字节上界、输出 token 上限和高峰费率保守预留；余额不足、HTTP 402 或预算不足时，同一工作流改用 `.env`。账本为 `.cache/deepseek-budget.sqlite`，跨重启和进程共享，不要删除来重置预算。

上限20元，可用 `DEEPSEEK_BUDGET_CNY` 调低；保守记账可能提前切换，账单以服务商为准。无法确认人民币余额也切到备用接口。费率快照日期2026-10-26，参考 [官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)，价格变化需维护 budget.py。切换记录实际模型，不能当成同模型控制实验。

## 汇报与测试

[六人分工](docs/team-project.md)、[评测方案](docs/evaluation.md)、[实际验证记录](docs/validation.md)。阅读配置支持 single、multi、verified；正式比较应人工标注正确率、证据支持率、覆盖率并报告 token 和时间，当前配置不是严格等预算消融。

```powershell
.venv/Scripts/python.exe -m pytest -q
```

自动测试不调用实际付费 API，也不执行第三方仓库。真实运行单独记录。PDF 当前只支持文本抽取；双栏、表格、图像和公式仍需人工核对，同一模型的多个角色可能共享偏差。

官方参考：[LangGraph](https://docs.langchain.com/oss/python/langgraph/graph-api)、[ReAct](https://github.com/ysymyth/ReAct)、[MAC-SQL](https://github.com/wbbeyourself/MAC-SQL)、[CHESS](https://github.com/ShayanTalaei/CHESS)、[KnowTrans](https://github.com/A11en0/KnowTrans)、[LLaVA](https://github.com/haotian-liu/LLaVA)。第三方源码不作为本项目原创代码分发，使用时遵守上游条款。
