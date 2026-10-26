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
