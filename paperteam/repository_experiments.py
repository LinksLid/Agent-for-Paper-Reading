"""Per-paper LangGraph: search -> download -> inspect -> plan -> execute -> assess."""
from contextlib import nullcontext
import json
from pathlib import Path
import re
import time
import math
import httpx
from typing import Literal, TypedDict

from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

from .experiment_runtime import Broker, DockerRuntime, docker_available, safe_relative
from .repositories import GitHub, inspect_repository, save_json, github_proxy
from .codex_backend import CodexPlanner
from .datasets import available_datasets, dataset_context, digest


def check_external_services(repo: Path):
    if not (repo / "wikienv.py").is_file():
        return {}
    check = {"url": "https://en.wikipedia.org/w/index.php?search=ReAct", "available": False}
    try:
        with httpx.Client(timeout=15, follow_redirects=True, trust_env=False, proxy=github_proxy()) as client:
            response = client.get(check["url"])
        check["http_status"] = response.status_code
        check["available"] = response.status_code == 200 and "mw-parser-output" in response.text
        if not check["available"]:
            check["reason"] = "Wikipedia 拒绝访问或未返回文章内容。不得绕过访问限制，也不得把错误页当检索结果计算有效指标。可选择原仓库其他具备条件的实验。"
    except httpx.HTTPError as error:
        check["error_type"] = type(error).__name__
        check["reason"] = "Wikipedia 连接失败，当前不能有效运行依赖实时检索的实验。"
    return {"wikipedia": check}


class ExperimentCommand(BaseModel):
    argv: list[str] = Field(min_length=2, max_length=80)
    cwd: str = "."
    timeout_seconds: int = Field(default=300, ge=10, le=600)
    source_file: str
    source_quote: str
    purpose: str


class CompatibilityPatch(BaseModel):
    path: str
    old: str = Field(min_length=1)
    new: str
    reason: str


class MetricSpec(BaseModel):
    name: str
    artifact: str
    json_path: str
    aggregate: Literal["mean", "sum", "count"] | None = None


class ExperimentPlan(BaseModel):
    runnable: bool
    scope: Literal["benchmark_subset", "inference_demo", "evaluation_only", "smoke_test", "blocked"]
    reason: str
    python_version: Literal["3.10", "3.11", "3.12"] = "3.11"
    data_source: str
    missing_requirements: list[str]
    uses_llm: bool
    completion_continuation: bool = False
    install: list[ExperimentCommand] = Field(max_length=4)
    run: list[ExperimentCommand] = Field(max_length=3)
    patches: list[CompatibilityPatch] = Field(default_factory=list, max_length=8)
    artifacts: list[str] = Field(max_length=12)
    metrics: list[MetricSpec] = Field(default_factory=list, max_length=12)
    differences_from_paper: list[str]


class ExperimentAssessment(BaseModel):
    summary: str
    next_steps: list[str]
    limitations: list[str]


class RepoState(TypedDict, total=False):
    paper: dict
    candidate: dict
    source: dict
    repo_path: str
    inventory: dict
    discovery: dict
    plan: dict
    status: str
    stage: str
    reason: str
    commands: list[dict]
    artifacts: list[str]
    metrics: list[dict]
    assessment: dict
    events: list[dict]
    attempts: int
    artifact_root: str


def validate_plan(plan: ExperimentPlan, inventory: dict):
    if plan.runnable and (not plan.run or plan.missing_requirements):
        raise ValueError("可运行计划必须有运行命令且没有未解决的前置条件")
    if not plan.runnable:
        return
    for command in plan.install + plan.run:
        safe_relative(command.cwd)
        source = inventory["contents"].get(command.source_file)
        if not source or not command.source_quote or command.source_quote not in source:
            raise ValueError("运行命令缺少仓库原文依据：" + command.source_file)
        if command.argv[0] not in {"python", "python3", "pip", "pip3", "pytest", "sh", "bash"}:
            raise ValueError("不支持该命令工具")
        if any("replace-me" in a or "YOUR_" in a or re.search(r"\{[A-Za-z_][A-Za-z0-9_]*\}", a) for a in command.argv):
            raise ValueError("运行命令仍包含未填参数")
    for patch in plan.patches:
        safe_relative(patch.path)
        if patch.path not in inventory["files"]:
            raise ValueError("兼容性补丁必须修改真实仓库文件")
    for path in plan.artifacts:
        safe_relative(path)


def extract_metrics(output: Path, specs: list[dict], artifacts: list[str]):
    metrics = []
    for spec in specs:
        relative = "artifacts/" + safe_relative(spec["artifact"])
        if relative not in artifacts:
            continue
        path = output / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 5_000_000:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if spec.get("aggregate"):
                if not isinstance(value, list) or not value:
                    continue
                values = []
                for row in value:
                    item = row
                    for part in spec["json_path"].split(".") if spec["json_path"] else []:
                        item = item[int(part)] if isinstance(item, list) else item[part]
                    if not isinstance(item, (int, float)) or isinstance(item, bool) or not math.isfinite(item):
                        raise ValueError("所有评测行必须有真实有限数值")
                    values.append(item)
                value = {"mean": lambda: sum(values) / len(values), "sum": lambda: sum(values),
                         "count": lambda: len(values)}[spec["aggregate"]]()
                metrics.append({**spec, "value": value, "sample_count": len(values), "provenance": relative})
                continue
            for part in spec["json_path"].split(".") if spec["json_path"] else []:
                value = value[int(part)] if isinstance(value, list) else value[part]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                metrics.append({**spec, "value": value, "provenance": relative})
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    return metrics


def build_repository_workflow(llm, github: GitHub, output: Path, samples=3, progress=None, planner=None, datasets=None, start_at="search"):
    if start_at not in {"search", "execute"}:
        raise ValueError("不支持该工作流起点")
    output.mkdir(parents=True, exist_ok=True)
    planner = planner if planner is not None else llm
    datasets = datasets or []
    first_record = len(getattr(llm, "records", []))

    def checkpoint(stage, fn):
        def wrapped(state):
            if progress:
                progress(stage, state.get("paper", {}).get("filename", ""))
            save_json(output / "progress.json", {"paper": state["paper"]["filename"], "stage": stage,
                                                 "status": "running", "events": state.get("events", [])})
            try:
                update = fn(state)
            except Exception as error:
                update = {"status": "failed", "reason": f"{stage} 阶段失败（{type(error).__name__}）：{str(error)[:700]}"}
            update["stage"] = stage
            update["events"] = state.get("events", []) + [{"stage": stage, "status": update.get("status", "running"), "time": time.time()}]
            save_json(output / "progress.json", {"paper": state["paper"]["filename"], "stage": stage,
                                                 "status": update.get("status", "running"), "reason": update.get("reason", ""), "events": update["events"]})
            return update
        return wrapped

    def discover(state):
        candidate, evidence = github.discover(state["paper"], llm, output)
        if not candidate:
            search_errors = any("error_type" in r for r in evidence["searches"])
            return {"status": "search_failed" if search_errors else "not_found", "discovery": evidence,
                    "reason": evidence.get("reason") or evidence.get("selection", {}).get("reason", "未找到可核验对应关系的仓库")}
        return {"candidate": candidate, "discovery": evidence, "status": "running"}

    def download(state):
        repo, source = github.download(state["candidate"], output)
        return {"repo_path": str(repo), "source": source, "status": "running"}

    def inspect(state):
        inventory = inspect_repository(Path(state["repo_path"]))
        inventory["datasets"] = dataset_context(datasets)
        inventory["external_services"] = check_external_services(Path(state["repo_path"]))
        save_json(output / "inspection.json", inventory)
        return {"inventory": inventory, "status": "running"}

    def plan(state):
        prompt = ("根据实际下载的论文仓库制定可执行实验计划。必须使用原仓库的实验入口、真实数据和评估代码，禁止另造玩具数据、"
                  "预填模型答案或把导入成功当作实验复现。当前最多计划 " + str(samples) + " 个真实样本。"
                  "环境：Docker CPU 容器，Python 3.10/3.11/3.12 可选，4GB 内存，暂无容器 GPU，"
                  "不自动下载大型模型、没有真实密钥。inventory.datasets 列出实际只读挂载的已核验公开数据及其容器路径；"
                  "其中 bird-dev 的 dev.json 是固定前N条真实开发集，dev_gold.sql 是对应官方金标准，dev_tables.json 为原始模式。"
                  "数据可直接作为原脚本参数使用，禁止向生成模型提供金标准 SQL。若 datasets 为空才表示无外部数据。"
                  "external_services 列出外部服务实际探测结果，服务不可用时不能伪装成已完成的性能评测。"
                  "如 ReAct 的 Wikipedia 访问被拒绝而已提供 alfworld 数据，优先执行 alfworld.ipynb 的原文本游戏实验。"
                  "ALFWorld 文本包不需要 Torch/视觉模型，使用已发布版本0.4.2（GitHub master 的0.5.0尚未发布）；get_environment 为当前 API，"
                  "旧Completion接聊天模型时可显式设置completion_continuation=true增加按原提示续写的system格式约束；"
                  "这会改变模型输入，必须记入differences_from_paper，不能当成原论文同提示条件。"
                  "旧 getattr(alfworld.agents.environment, ...) 可做等价接口适配；PDDL逻辑从已安装包的 alfworld.info 路径读取。"
                  "保留原动作循环和提示、原环境 won 判定，只缩小外层 episode 数；不额外使用 expert 动作解题。"
                  "需要本地大模型/未提供数据/其他服务而无法解决时 runnable=false，列出精确缺口及位置，不伪造运行。"
                  "数据在仓库中可用时优先做小规模原实验；API 需要时 uses_llm=true，框架提供兼容 OpenAI 的临时代理，"
                  "通过 OPENAI_BASE_URL/OPENAI_API_BASE/OPENAI_API_KEY 环境变量使用，不得硬编码外部服务密钥。"
                  "代理支持非流式 chat/completions 和旧 completions，不支持工具调用、embedding 或视觉。"
                  "所有 install/run 命令给 argv 数组，source_file 与 source_quote 必须逐字对应提供的仓库内容。"
                  "允许 patches 对原脚本做 API/路径/样本上限兼容修改，但不能改变算法、数据标签、评估或伪造指标；"
                  "old 必须唯一匹配原文件，新输出文件名用相对路径。不可凭空引入作者未提供的训练模型或数据。"
                  "Notebook 补丁 old/new 使用解码后的代码单元正文，不要写 JSON 转义字符。"
                  "对 Notebook 安装 nbconvert 和 ipykernel 即可，无需完整 jupyter 元包，可用 python -m jupyter nbconvert --execute 执行；"
                  "原代码含 openai.Completion.create 或 openai.ChatCompletion.create 时，安装 openai<1 或0.28版本；gym配合numpy<2。"
                  "将原循环的500样本改为目标数量，允许在原脚本末尾导出实际计算的分数/预测到JSON。"
                  "能够由 install、临时API代理或兼容补丁自动解决的事项，不应列入 missing_requirements。"
                  "scope 如只有帮助命令或单元测试必须为 smoke_test，不能标成 benchmark_subset。"
                  "metrics 只能指定实际产出 JSON 中的数值键，json_path 以点分隔；不能给模型估计分数。"
                  "脚本输出不是 JSON 时 metrics=[]，保留原始输出供人工核对。"
                  "install/run/artifacts 无法确认时留空，不执行未经证实的命令。\n论文：" + state["paper"]["filename"] +
                  "\n仓库检查结果：" + json.dumps(state["inventory"], ensure_ascii=False))
        result = planner.structured("复现/原仓库实验规划", prompt, ExperimentPlan, max_tokens=6500)
        save_json(output / "plan.json", result.model_dump())
        if result.runnable and (not result.run or result.missing_requirements):
            result = result.model_copy(update={"runnable": False, "scope": "blocked",
                                              "reason": "计划中仍有未解决条件，未执行：" + result.reason})
            save_json(output / "plan.json", result.model_dump())
        validate_plan(result, state["inventory"])
        return {"plan": result.model_dump(), "status": "running" if result.runnable else "blocked",
                "reason": result.reason if not result.runnable else ""}

    def execute(state):
        plan = ExperimentPlan.model_validate(state["plan"])
        wiki = state["inventory"].get("external_services", {}).get("wikipedia", {})
        if wiki and not wiki.get("available") and any(
                arg.lower().endswith(("hotpotqa.ipynb", "fever.ipynb")) for command in plan.run for arg in command.argv):
            return {"status": "blocked", "reason": "Wikipedia 外部依赖不可用，未将错误页产生的分数计为有效论文实验。"}
        if not docker_available():
            return {"status": "blocked", "reason": "需要 Linux Docker 引擎执行原仓库；当前 Docker 未就绪。源码和实验计划已保存。"}
        calls_per_sample = 55 if any(arg.lower().endswith("alfworld.ipynb") for step in plan.run for arg in step.argv) else 14
        broker_context = Broker(llm, max_calls=max(8, samples * calls_per_sample),
                                completion_continuation=plan.completion_continuation) if plan.uses_llm else nullcontext(None)
        commands, artifacts = list(state.get("commands", [])), []
        attempt_output = output if not state.get("attempts") else output / ("retry-" + str(state["attempts"]))
        with broker_context as broker:
            runtime_options = {"datasets": datasets} if datasets else {}
            if any("alfworld" in arg.lower() or "textworld" in arg.lower() for command in plan.install for arg in command.argv):
                runtime_options["build_tools"] = True
            with DockerRuntime(Path(state["repo_path"]), attempt_output, plan.python_version, broker, **runtime_options) as runtime:
                for patch in plan.patches:
                    runtime.patch(patch.path, patch.old, patch.new)
                save_json(output / "compatibility_patches.json", [p.model_dump() for p in plan.patches])
                for phase, steps in [("install", plan.install), ("experiment", plan.run)]:
                    for step in steps:
                        command = runtime.command(step.argv, step.cwd, step.timeout_seconds, phase)
                        command["attempt"] = state.get("attempts", 0)
                        commands.append(command)
                        save_json(output / "execution_history.json", commands)
                        if command["exit_code"]:
                            artifacts = runtime.collect(plan.artifacts)
                            return {"commands": commands, "artifacts": artifacts, "artifact_root": str(attempt_output),
                                    "status": "failed", "reason": phase + " 命令失败；见 commands.json 原始输出"}
                notebooks = [arg for command in plan.run for arg in command.argv if arg.endswith(".ipynb")]
                for command in plan.run:
                    if "--output" in command.argv:
                        index = command.argv.index("--output") + 1
                        if index < len(command.argv):
                            name = command.argv[index]
                            notebooks.append(name if name.endswith(".ipynb") else name + ".ipynb")
                artifacts = runtime.collect(list(dict.fromkeys(plan.artifacts + notebooks)))
        return {"commands": commands, "artifacts": artifacts, "artifact_root": str(attempt_output), "status": "running"}

    def repair(state):
        evidence = [{**{k: c[k] for k in ("phase", "argv", "exit_code") if k in c},
                     "stdout": c.get("stdout", "")[-9000:], "stderr": c.get("stderr", "")[-6500:]}
                    for c in state.get("commands", []) if c.get("phase") == "experiment" or c.get("exit_code")][-4:]
        prompt = ("原仓库实验发生了实际错误。根据日志修复依赖版本/API兼容/路径/运行参数，最多再尝试一次。"
                  "不得替换真实数据、改算法、伪造结果或硬编码答案。旧代码 openai.Completion.create 或"
                  "openai.ChatCompletion.create 需要 openai<1；旧 gym 推荐 numpy<2。框架提供旧completions与chat代理。"
                  "缺少无法自动取得的数据或模型时 runnable=false 并准确说明。"
                  "完整返回新的实验计划，保留仍需要的样本上限和结果导出补丁。\n原计划：" +
                  json.dumps(state["plan"], ensure_ascii=False) + "\n失败命令：" +
                  json.dumps(evidence, ensure_ascii=False) +
                  "\n仓库：" + json.dumps(state["inventory"], ensure_ascii=False))
        revised = planner.structured("复现/根据运行错误修复", prompt, ExperimentPlan, max_tokens=6500)
        validate_plan(revised, state["inventory"])
        save_json(output / "repaired_plan.json", revised.model_dump())
        return {"plan": revised.model_dump(), "attempts": state.get("attempts", 0) + 1,
                "status": "running" if revised.runnable else "blocked", "reason": "" if revised.runnable else revised.reason}

    def assess(state):
        specs = state["plan"]["metrics"]
        if not specs and state.get("candidate", {}).get("url") == "https://github.com/wbbeyourself/MAC-SQL":
            evaluation = next((p.removeprefix("artifacts/") for p in state.get("artifacts", [])
                               if p.endswith("/eval_result_dev.json")), None)
            if evaluation:
                specs = [{"name": name, "artifact": evaluation, "json_path": "res", "aggregate": aggregate}
                         for name, aggregate in [("execution_accuracy", "mean"), ("correct", "sum"), ("samples", "count")]]
        metrics = extract_metrics(Path(state.get("artifact_root", str(output))), specs, state.get("artifacts", []))
        scope = state["plan"]["scope"]
        status = "smoke_passed" if scope == "smoke_test" else "completed" if state.get("artifacts") else "executed"
        if scope in {"benchmark_subset", "evaluation_only"} and not metrics:
            status = "executed"
        commands = [{**{k: c[k] for k in ("phase", "argv", "exit_code", "seconds") if k in c},
                     "stdout": c.get("stdout", "")[-3000:], "stderr": c.get("stderr", "")[-1500:]}
                    for c in state["commands"]]
        actual_models = list({(r["provider"], r["model"]): {"provider": r["provider"], "model": r["model"]}
                             for r in getattr(llm, "records", [])[first_record:]
                             if r.get("agent") == "原仓库实验/API" and r.get("status") == "ok"}.values())
        evidence = {"scope": scope, "status": status, "commands": commands, "artifacts": state.get("artifacts", []),
                    "metrics": metrics, "actual_models": actual_models, "datasets": state["inventory"].get("datasets", []),
                    "completion_continuation": state["plan"].get("completion_continuation", False)}
        assessment = llm.structured("复现/实验结果核对", "只根据命令日志、实际产物和解析到的指标说明完成范围。"
                                    "不编造指标，不把 smoke_test 当论文实验，不把其他模型运行结果称为论文完整复现。\n" +
                                    "代码或日志中的请求模型名称可能只是代理前的别名，实际模型必须以 actual_models 为准。"
                                    "抽样方式和数据库覆盖以 datasets 的 source/selection/descriptor 为准。\n" +
                                    json.dumps(evidence, ensure_ascii=False), ExperimentAssessment)
        return {"status": status, "metrics": metrics, "assessment": assessment.model_dump()}

    graph = StateGraph(RepoState)
    functions = {"search": discover, "download": download, "inspect": inspect, "plan": plan, "execute": execute, "assess": assess}
    stages = list(functions)
    for stage, fn in functions.items():
        graph.add_node(stage, checkpoint(stage, fn))
    graph.add_edge(START, start_at)
    for current, following in zip(stages, stages[1:]):
        if current == "execute":
            continue
        graph.add_conditional_edges(current, lambda s, next_stage=following: next_stage if s.get("status") == "running" else END)
    graph.add_node("repair", checkpoint("repair", repair))
    graph.add_conditional_edges("execute", lambda s: "assess" if s.get("status") == "running" else
                                "repair" if s.get("status") == "failed" and not s.get("attempts") and
                                s.get("reason", "").startswith(("install 命令", "experiment 命令")) else END)
    graph.add_conditional_edges("repair", lambda s: "execute" if s.get("status") == "running" else END)
    graph.add_edge("assess", END)
    return graph.compile()


def run_repository_experiments(papers, llm, root: Path, output: Path, samples=3, progress=None, backend="api"):
    if backend not in {"api", "codex"}:
        raise ValueError("实验规划后端必须是 api 或 codex")
    output.mkdir(parents=True, exist_ok=True)
    planner = CodexPlanner(output / "codex") if backend == "codex" else llm
    datasets = available_datasets(root, samples)
    save_json(output / "datasets.json", [{k: v for k, v in r.items() if k != "host_path"} for r in datasets])
    github = GitHub(root)
    results = []
    started = time.monotonic()
    try:
        for paper in papers:
            first_record = len(llm.records)
            folder = output / paper["paper_id"]
            first_codex_record = len(planner.records) if backend == "codex" else 0
            graph = build_repository_workflow(llm, github, folder, samples, progress, planner=planner, datasets=datasets)
            (folder / "workflow.mmd").write_text(graph.get_graph().draw_mermaid(), encoding="utf-8")
            state = graph.invoke({"paper": paper, "events": []})
            result = {"paper_id": paper["paper_id"], "filename": paper["filename"], "status": state["status"],
                      "stage": state["stage"], "reason": state.get("reason", ""), "repository": state.get("candidate", {}).get("url"),
                      "commit": state.get("source", {}).get("commit"), "source_path": state.get("repo_path"),
                      "scope": state.get("plan", {}).get("scope"), "plan": state.get("plan"),
                      "commands": state.get("commands", []), "artifacts": state.get("artifacts", []),
                      "artifact_root": state.get("artifact_root"), "repair_attempts": state.get("attempts", 0),
                      "metrics": state.get("metrics", []), "assessment": state.get("assessment"),
                      "events": state["events"], "full_paper_reproduced": False,
                      "planning_backend": backend,
                      "completion_continuation": state.get("plan", {}).get("completion_continuation", False),
                      "datasets": dataset_context(datasets),
                      "codex_calls": len(planner.records) - first_codex_record if backend == "codex" else 0}
            result["experiment_models"] = list({(r["provider"], r["model"]): {"provider": r["provider"], "model": r["model"]}
                                                 for r in llm.records[first_record:] if r.get("agent") == "原仓库实验/API" and r.get("status") == "ok"}.values())
            save_json(folder / "result.json", result)
            results.append(result)
            save_json(output / "reproduction.json", summarize(results, started))
    finally:
        github.close()
    return summarize(results, started)


def summarize(results, started):
    completed = sum(r["status"] == "completed" for r in results)
    status = "completed" if completed == len(results) and results else "partial" if completed else "failed" if any(r["status"] == "failed" for r in results) else "blocked"
    return {"kind": "repository_experiments", "status": status, "papers": results,
            "downloaded": sum(bool(r.get("commit")) for r in results), "completed": completed,
            "codex_calls": sum(r.get("codex_calls", 0) for r in results),
            "seconds": round(time.monotonic() - started, 3), "full_paper_reproduced": False}


STATUS_LABELS = {"completed": "实验已完成", "executed": "已运行，待核对评估", "smoke_passed": "仅环境检查通过",
                 "blocked": "实验条件不足", "failed": "执行失败", "not_found": "未找到已核实仓库", "search_failed": "搜索未完成",
                 "disabled": "未启用", "partial": "部分实验已完成", "unsupported": "旧版未支持"}


def reproduction_markdown(result):
    lines = ["逐篇搜索并核对 GitHub 仓库，下载固定提交，再依据原仓库规划和尝试执行实验。", "",
             "| 论文 | GitHub 仓库 | 实验状态 |", "|---|---|---|"]
    for paper in result.get("papers", []):
        name = paper["filename"].replace("|", " ")
        repo = f"[源码]({paper['repository']})" if paper.get("repository") else "尚未核实"
        lines.append(f"| {name} | {repo} | {STATUS_LABELS.get(paper['status'], paper['status'])} |")
    lines.append("")
    for paper in result.get("papers", []):
        lines.extend(["### " + paper["filename"], ""])
        if paper.get("commit"):
            lines.append(f"- 已下载源码提交：`{paper['commit']}`。")
        if paper.get("scope"):
            lines.append("- 本次计划范围：`" + paper["scope"] + "`。")
        if paper.get("planning_backend"):
            lines.append("- 规划与修复后端：" + ("Codex CLI" if paper["planning_backend"] == "codex" else "现有 API") + "。")
        if paper.get("experiment_models"):
            lines.append("- 实际实验模型：" + "、".join(m["model"] for m in paper["experiment_models"]) + "。")
        if paper.get("completion_continuation"):
            lines.append("- 显式启用旧 Completion 续写格式约束；这是兼容性对照，模型输入与原论文有差异。")
        if paper.get("reason"):
            lines.append("- 当前情况：" + paper["reason"])
        for missing in (paper.get("plan") or {}).get("missing_requirements", []):
            lines.append("- 缺少条件：" + missing)
        for metric in paper.get("metrics", []):
            lines.append(f"- 实测 {metric['name']}：{metric['value']}（产物：`{metric['provenance']}`）。")
        if paper.get("assessment"):
            lines.append("- 结果核对：" + paper["assessment"]["summary"])
        lines.append("")
    return lines
