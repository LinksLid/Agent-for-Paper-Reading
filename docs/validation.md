# 实际验证记录

2026-10-27：复现已改为逐篇 GitHub 搜索、核验、源码下载及原仓库实验。新版记录见本文末尾；以下2026-10-26的合成数据验证为历史记录，不能解释为原论文实验复现。

日期：2026-10-26。自动测试与真实模型运行分别记录。

## 自动测试

最终本机执行：`python -m pytest -q`，**23 passed**。

覆盖：原文定位、跨论文错引、检索缺口误写为事实、并行汇合、核验退回、定向补查、按原角色返工、持久化检查点、API 请求上限、JSON 格式重试、日志脱敏、持久化预算、HTTP 402 自动切换、低余额提前切换、只读 SQL、SQL 错误修正、修正次数上限、源码哈希及 Windows 数据库句柄释放、Streamlit 界面加载。

这些测试使用模拟 API，不代表真实论文分析的准确率。

## 五篇论文的真实运行

运行配置：`verified`，最多补查 1 轮，同时启用 MAC-SQL 微型实验。模型 `deepseek-flash`；请求显式关闭思考模式。

| 项目 | 观测结果 |
|---|---:|
| PDF 数量 | 5 |
| PDF 总页数 | 132 |
| API 请求次数 | 33 |
| 总 token | 134,842 |
| 用时 | 105.829 秒 |
| 最终接受结论 | 28 |
| 最终仍未通过的结论 | 2 |
| 核验轮次 | 2 |

第一轮接受 26 条、退回 5 条；补查替换相应角色的卡片后，第二轮接受 28 条、退回 2 条。两轮不是同一集合上的简单加减，不能据此计算“修复率”。其中一次语义退回指出：引用只支持单元测试的打分方式比较，不能支持同一句中关于另一个消融结论的判断。这是模型核验意见，仍应人工复查。

未通过的结论没有进入最终报告。通过核验也不等同于人工确认无误；尚未完成人工事实正确率、关键点覆盖率和跨模型核验实验。

清单见 [`examples/reading-run.json`](../examples/reading-run.json)。完整报告、原文证据及检查点保存在本地 `output/demo-all-papers/`，不随代码发布论文全文。

## MAC-SQL 方法级微型实验

- 官方来源提交：`31a9df5e0d520be4769be57a4b9022e5e34a14f4`。
- 6 类问题、2 个固定数据库变体，参考 SQL 不进入生成提示词。
- 单次基线 EX：6/6；Decomposer 与按需 Refiner 流程 EX：6/6。
- 两组多重集比较也均为 6/6。此次模型生成均可执行，真实运行未触发 Refiner；修正路径由注入错误的离线测试验证，不能称为本次模型自动纠错的实测成绩。
- 小模式绕过 Selector，未运行正式 BIRD/Spider，不是论文完整复现，不支持多智能体优于单次基线的结论。

逐样本预测与评估见 [`examples/macsql-result.json`](../examples/macsql-result.json)。另以锁定源码中的官方 SQL 解析器与 EX 函数检查 6 条参考 SQL，均通过；同时确认 Windows 文件句柄可以释放。

## API 与预算

- 同济接口曾返回“当前网络环境不支持”；用户关闭虚拟网卡后恢复。
- 同济接口并发上限为 2；程序使用每进程单请求并发与有限退避重试。多个独立进程仍可能共同占满服务端配额。
- DeepSeek 优先的五篇运行完成后，本项目两次真实阅读运行合计的高峰费率保守预算记账为约 0.540296 元，低于 20 元上限；这不是服务商实际账单。
- 通过暂时调低内存中的测试阈值，真实验证预算不足后转到同济，并取得 `OK` 响应。没有为测试耗尽 DeepSeek 账户，也没有改写用户的配置文件。HTTP 402 路径使用模拟响应测试。
- 两份真实配置、余额账本、PDF 原文件和下载的第三方源码均由 `.gitignore` 排除。

## 2026-10-27：逐篇 GitHub 与原仓库实验

新版自动测试为 **42 passed**，新增仓库 URL、压缩包路径、Notebook 代码单元补丁、原文命令依据、真实 JSON 指标、非法/非有限数值过滤、临时 API 代理与旧 Completion 截断语义等覆盖。另在真实 Docker Desktop 中验证 tmpfs 文件向挂载产物目录的复制，未调用付费 API。

逐篇流程已搜索并核验五个对应仓库，全部下载固定提交；记录见 [`examples/repository-results.json`](../examples/repository-results.json)：

| 论文 | 已核实仓库 | 本次结果 |
|---|---|---|
| CHESS | ShayanTalaei/CHESS | 缺 BIRD 开发集及 SQLite 数据库、模式与预处理资源 |
| KnowTrans | A11en0/KnowTrans | 缺 Jellyfish 数据、上游模型/LoRA 权重和相应训练推理条件 |
| MAC-SQL | wbbeyourself/MAC-SQL | 缺 README 要求的 BIRD/Spider 数据包和数据库 |
| ReAct | ysymyth/ReAct | 原 hotpotqa.ipynb 已在容器内执行，2 个真实样本，EM 为 0/2 |
| Visual Instruction Tuning | haotian-liu/LLaVA | 缺模型权重、图像数据与 GPU 等运行条件 |

ReAct 使用作者仓库自带的 `data/hotpot_dev_v1_simplified.json` 和原 `HotPotQAWrapper` 评估，保留原随机种子，循环由 500 改为 2，并在末尾导出实际计算的 EM。依赖适配为旧版 `openai<1`、`numpy<2` 等；模型通过预算代理替换为 DeepSeek，因此不是论文 GPT-3/PaLM 指标复现。0/2 是实测结果，不因结果低而改动数据或评分。

真实运行暴露并解决了三类工程问题：旧 OpenAI SDK 接口不兼容；容器只读根目录下缓存应写到 tmpfs；Docker Desktop archive/cp 无法稳定读取 tmpfs 文件，改为容器内复制到挂载的产物目录。依赖失败后的修复由运行日志驱动的 LangGraph 分支完成。

最终 `demo-all-papers` 的复现状态为 partial：下载5篇，完成1篇小规模原实验，其他4篇保留具体阻塞条件。完整原始命令、兼容补丁、产物和 API 用量在本地 `output/demo-all-papers/repository-experiments-*/`。历史合成数据成绩与当前原仓库结果分别保留，不混称为论文复现。

## 2026-10-27：可选 Codex CLI 接入

新增 `--reproduction-backend codex` 与界面「复现实验规划与修复」选项。Codex 接管原仓库计划与一次日志修复；其余阅读、搜索核对、实验模型请求和结果解释仍走现有 API，实际第三方代码仍只在 Docker 执行。

本机检测到原生 CLI 0.164.0-alpha.11.1、npm CLI 0.147.0；`codex login status` 显示 ChatGPT 登录。原生 CLI 帮助与特性列表确认所需参数可用。仅检查安装和登录状态，**没有发起真实 Codex 推理或重新运行论文实验**，原有五篇实验结果没有被改写。

本轮完整自动测试 **50 passed in 4.45s**。新增八项测试覆盖：嵌套 JSON Schema、API 凭据与父会话环境过滤、结构化输出和独立记账、调用上限、非法输出、错误日志脱敏、超时停止、缺数据停止及 Codex 规划/修复与 API 核对之间的路由。CLI 和 Docker 执行均为模拟，测试准确率不是论文结果。

Windows 受限沙箱禁止本机回环连接，导致 Streamlit 创建事件循环时等待 socketpair，原有临时 HTTP 代理测试也被拒绝。允许本地测试所需回环连接后，使用项目缓存目录作 pytest 临时目录，全部通过：

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.cache/pytest-codex-final-20261027 --tb=short
```

Codex 每次流程最多调用10次，每次最多300秒；消耗自己的登录账户额度，独立保存用量，未计入 DeepSeek 20元保护账本。真实端到端 Codex 实验效果仍待恢复实验后验证，不能由离线测试推出复现能力提升或完整论文指标复现。

## 2026-10-27：恢复后的真实实验结果

以上是接入时的暂停快照。本节记录恢复后实际执行的结果。五个对应仓库均已下载并校验固定提交；Codex CLI 实际调用13次（包含失败及超时），规划与工程修复后完成两项真实子集实验。最终本地回归测试为 **60 passed**，包含真实结果加载、对照表与产物下载的界面检查。公开汇总见 [`examples/repository-results.json`](../examples/repository-results.json)。

| 论文/条件 | 实际数据与入口 | 结果 |
|---|---|---|
| MAC-SQL 先导运行 | 官方 BIRD dev 前6条，作者 run.py 和 evaluation_bird_ex.py | EX 5/6，83.33% |
| MAC-SQL 扩大运行 | 官方 BIRD dev 前30条，同一原生成与评测入口 | EX **20/30，66.67%** |
| ReAct 直接转发旧 Completion | ALFWorld valid_unseen，作者 alfworld.ipynb，6个真实任务 | 成功0/6；294次动作反馈均为 Nothing happens |
| ReAct 续写接口兼容对照 | 相同6个官方任务，增加显式文本续写 system 约束 | 成功 **1/6，16.67%**；272次动作中25次反馈为 Nothing happens |
| CHESS | 已提供官方 BIRD 数据并检查实际配置、预处理实现 | 缺少 text-embedding-3-large 服务及对应检索索引条件，未执行原推理 |
| KnowTrans | 已核对原训练/推理入口 | 原基座/LoRA权重及训练推理条件未满足，未执行原实验 |
| LLaVA | 已核对原推理/评测入口 | 原权重、评测图像及相应运行条件未满足，未执行原实验 |

**实际实验模型均为 deepseek-flash。** 原代码日志中的 gpt-4-1106-preview、text-davinci-002 是代理前的请求别名。已按宿主 API 记录校正结果说明，并保留校正前的模型解释，数值、预测、Notebook及原始日志均未改动。不能把请求别名写成实际使用了 GPT-4 或原论文模型。

BIRD 来自[官方开发集入口](https://bird-bench.github.io/)，下载346,207,293字节的原包并记录SHA256；只解压所需数据库。30条为原 dev.json 顺序中的 question_id 0—29，全部来自 california_schools，未按成绩筛选。金标准保留给原评测脚本，生成阶段不传入金标准 SQL；未使用 gold-schema 或跳过 Selector。最终 EX 直接聚合原 eval_result_dev.json 的全部30个 res 数值，保留0分样本。难度分布为21条 simple、8条 moderate、1条 challenging，分别答对17、3、0条。单库子集不能代表全量1534条或跨库泛化。

ALFWorld 使用[官方文本环境数据](https://github.com/alfworld/alfworld)，提取 valid_unseen 的134个游戏，不下载视觉检测器或策略模型。当前 Wikipedia 返回403 robots限制，因此本轮不把 HotpotQA/FEVER 错误页产生的分数当作有效检索成绩。ALFWorld 安装使用已发布的0.4.2；0.5.0仅出现在上游master版本声明，PyPI尚未发布。Jericho/TextWorld需要编译工具，采用独立构建镜像后仍在只读根目录、4GB内存、2CPU的运行容器中执行。

ReAct 对照固定为基线实际运行的同一组6个任务标识，执行顺序不同；保留原提示示例、动作循环、每任务49步上限和环境 won 成功判定，不使用专家动作。唯一主动调整的模型接口条件为增加文本续写格式 system 消息。原调用方式输出普通解释文字，续写兼容后开始输出 go to、take、open 等环境动作，完成了1个任务。该对照改变了模型输入，且样本很少、未做重复运行，不能推断论文方法整体性能提高或与论文指标一致。

本轮解决并记录的核心问题：

1. **正式数据与静默失败**：原 MAC-SQL 把绝对数据库路径开头斜线去掉，造成6条全部找不到数据库；run.py又捕获异常并以0退出，评测才暴露空预测。通过容器内相对路径别名解决，并让修复节点获得生成及评测日志、保留失败产物。成功判定依赖实际预测与指标。
2. **环境与可恢复执行**：区分未发布包版本、缺 make/gcc 与方法失败；固定已发布依赖，构建独立编译环境。基础设施修复后逐文件校验源代码哈希并复用已保存计划，避免重复规划；重跑保留原计划来源和依赖修正记录。
3. **协议与语义兼容**：旧 Completion 转成聊天请求后可能返回讲解而不是动作；显式续写约束作为独立条件对照。报告同时保留0/6基线和1/6结果，实际模型从宿主记录读取，指标从原产物解析。

同济 text-embedding-3-large 最小请求返回HTTP400，未取得向量；记录在最终实验目录的 embedding_check.json。当前没有满足 CHESS 原方法的可用 embedding 路径，未通过删除检索模块或替换评分来制造完成状态。

累计人民币预算账本保守估算 **5.339484元**，包含此前项目调用，低于20元上限；并非服务商实际账单。未通过耗尽余额测试切换，本次实验模型记录未出现同济推理模型。Codex 使用其自己的登录账户额度，单独记录。

最终阅读报告与界面已更新至 `output/demo-all-papers/`，汇总为 `repository-experiments-final-20261027/reproduction.json`。完整预测、评分明细、Notebook、兼容补丁、镜像ID、命令及失败记录仍保存在各尝试目录；数据、密钥和第三方源码均不提交。最终状态为 partial：**完成2项子集实验，3篇条件不足，全部 full_paper_reproduced=false**。
