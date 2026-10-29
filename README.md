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

阅读结果默认进入 **先读懂论文**，逐篇按以下顺序讲解：

1. **任务与输入输出**：谁拿到什么材料、需要交付什么结果；原来怎么做、卡在哪里。
2. **一个小例子**：用明确标注的假想输入走一遍方法，帮助理解；教学推演没有实际运行。
3. **方法流程**：展示经核验的数据流图，每步说明接收什么、执行什么、交付什么；区分训练、推理和评测。
4. **具体改进对照**：逐项列出原有做法、本文做法、改动原因及对应实验依据。系统总分不自动等于每个组件的独立贡献。
5. **验证与边界**：作者报告和本机实验分别展示，附通俗术语解释；可选择原文依据、PDF物理页码及页图。

这层讲解由综合报告员整理，调用证据核验员逐项复核，缺口触发最多一次补查。字段、图中节点和连线都需要原文支持；未通过核验的事实不进入主讲解。教学示例另做方法一致性检查。这里的“8项齐备”只表示讲解结构满足检查条件，不表示覆盖论文全部内容或人工确认正确。

已保存的历史阅读可以点击 **生成全部论文的讲解** 单独升级；已有讲解时，选择论文后点击 **更新这篇论文的讲解**，只更新所选论文并保留其他讲解与原实验记录。生成前核对PDF哈希；原文页图使用可选的 Poppler `pdftoppm`，未安装时仍可查看引用与下载原PDF。导出的 Markdown 包含 Mermaid 方法图和引用。五篇论文的完整阅读加讲解建议将本次 API 调用上限设为120；实际调用量受补查次数影响，累计20元预算仍生效。

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

阅读：PDF 按页索引 → 规划 → 方法/实验并行分析 → 原文定位与语义核验 → 定向补查 → 任务/方法/改进讲解与核验 → 逐篇实验 → 报告与比较结论核验。

新增讲解是综合报告员的子流程，仍为六个角色。默认 `verified` 配置启用；单智能体与仅分工基线保留各自流程，避免将额外核验调用混进基线。对历史结果单独升级时，新增API调用与耗时单独记录，不能混作原基线耗时。

实验是独立 LangGraph 子图：**搜索 → README 对应关系核验 → 下载固定提交 → 检查仓库 → 制定原实验计划 → 容器执行 → 核对产物**。命令失败后，根据真实日志修复最多一次，再尝试执行。

既检查论文内的链接，也按准确标题、方法名和作者搜索；README 必须提供对应关系的原文，排除被引用的依赖库和无关同名项目。搜索失败不会被解释为论文没有代码。搜索和源码缓存复用一天，复用源码时核对文件哈希。GitHub 公共 REST 额度不足时，可用只读 Git ref 查询获取公开仓库提交。

## 如何运行原实验

使用原仓库脚本或 Notebook、真实数据与评估逻辑。允许记录清楚的依赖版本、API、路径、样本上限和结果导出兼容补丁；不改算法、数据标签、金标准或评分函数来制造成功。Notebook 直接执行修改后的代码单元，保留输出。

Docker 使用只读根目录、2 CPU、4GB 内存、进程数限制和最小权限。只挂载对应源码（只读）和本次输出目录；主项目、PDF、两份真实配置和 Docker socket 均不挂载。容器结束后删除，源码和日志保留。Docker 不可用时保留计划和原因，不回退到无操作系统隔离的本机执行。

原代码只获得临时 API 代理 token，真实密钥保留在主进程；代理继续执行累计20元预算和调用次数限制。支持非流式文本 chat/completions、旧 completions 与 Azure 风格 chat 路径；暂不支持 embedding、视觉、工具调用和流式。容器未配置 GPU，不自动下载大型模型权重。缺数据、权重或其他服务时，逐项列出名称和路径。

### 官方数据与失败重跑

```powershell
# BIRD 首次下载约346MB；仅解压固定前N条所需数据库，并记录源包及文件哈希
.venv/Scripts/python.exe -m paperteam fetch-data --dataset bird --samples 6
# 更大的正式数据子集可直接通过CLI或界面设置目标样本数（1—100）
.venv/Scripts/python.exe -m paperteam fetch-data --dataset bird --samples 30
.venv/Scripts/python.exe -m paperteam reproduce --paper MAC-SQL --samples 30 --max-calls 200 --reproduction-backend codex
# 首次下载约143MB的 ALFWorld 官方文本数据，仅提取 valid_unseen；不下载视觉模型
.venv/Scripts/python.exe -m paperteam fetch-data --dataset alfworld
# 对 output 中的已完成/失败实验目录复用原计划；先核对每个源码文件和数据哈希
.venv/Scripts/python.exe -m paperteam.resume_experiment output/your-run/paper-id --samples 6
```

数据保存在 `external/datasets/`，作为独立只读挂载提供给容器，不复制到下载的源码里，也不传入真实配置。`/work/datasets` 是容器内指向 `/datasets` 的兼容别名，解决部分原仓库去掉绝对路径开头斜线的问题。第三方代码仍不能访问主项目和真实密钥。失败时也尝试保留已有产物，修复模型同时获得生成和评测日志，避免只看到“预测为空”的后续错误。

ALFWorld 的 Jericho/TextWorld 依赖编译工具；框架会用 `docker/python-tools.Dockerfile` 构建独立运行镜像。构建上下文只有 docker 目录，运行阶段仍使用只读根目录、4GB内存和2CPU限制。HotpotQA/FEVER 的 Wikipedia 入口若返回403，记录依赖不可用，不将错误页当检索内容计算有效性能。

`resume_experiment --pin alfworld==0.4.2` 只修正原计划已包含的依赖版本，并保存修改来源。`--completion-continuation` 显式为旧 Completion 请求增加“按输入继续补全”的 system 格式约束；原始聊天请求不变。该设置会改变模型输入，必须作为兼容性对照单独记录，不能称作原论文完全相同的提示设置。可配合 `--match-episodes <baseline/artifacts/alfworld_results.json>` 复用基线实际执行的相同任务标识；不提供专家动作或答案。

MAC-SQL 的执行准确率直接从原 `eval_result_dev.json` 的全部 `res` 字段聚合；包含失败样本，不丢弃0分，不用模型估分。30条实验只覆盖官方开发集顺序中的首个数据库，不能外推全量1534条或跨库泛化效果。完整指标和失败记录见 [验证记录](docs/validation.md)。

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

逐篇讲解及每轮核验保存在 `explanations/<paper-id>/`，历史升级使用 `explanations-<时间>/`；`state.json.paper_guides` 保存可展示内容、逐项核验结果、讲解结构缺口与补查历史。结构化输出的字段错误只记录路径和错误类型，不保存完整原始响应；机械断词恢复另记原生成引文与连续原文，之后仍做语义核验。

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
