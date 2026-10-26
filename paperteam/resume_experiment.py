"""Re-execute a saved Codex plan after fixing infrastructure, with source checks."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time
import uuid

from .codex_backend import CodexPlanner
from .config import ROOT, Settings
from .datasets import available_datasets, dataset_context, digest
from .llm import LLM
from .repositories import GitHub, inspect_repository, repo_name, save_json
from .repository_experiments import (ExperimentPlan, build_repository_workflow, check_external_services,
                                     summarize, validate_plan, CompatibilityPatch)


def resume(previous: Path, settings: Settings, samples=6, progress=print, pins=None, initial_plan=False,
           completion_continuation=False, match_episodes: Path | None = None):
    previous = previous.resolve()
    root = settings.root
    if not previous.is_relative_to((root / "output").resolve()):
        raise ValueError("只能重跑当前项目 output 中的已保存计划")
    old = json.loads((previous / "result.json").read_text(encoding="utf-8"))
    source = json.loads((previous / "source.json").read_text(encoding="utf-8"))
    name, commit = repo_name(source["url"]), source["commit"]
    if not name or name != source["repository"] or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("源码来源记录无效")
    repo = (root / "external/repos" / name.replace("/", "--") / commit[:12]).resolve()
    if not source.get("files"):
        raise ValueError("缺少源码校验清单")
    for name, sha in source["files"].items():
        path = (repo / name).resolve()
        if not path.is_relative_to(repo) or not path.is_file() or digest(path) != sha:
            raise ValueError("保存后源码发生变化，不复用旧计划：" + name)
    resources = available_datasets(root, samples)
    inventory = inspect_repository(repo)
    inventory["datasets"] = dataset_context(resources)
    inventory["external_services"] = check_external_services(repo)
    candidates = [previous / "plan.json"] if initial_plan else [previous / "repaired_plan.json", previous / "plan.json"]
    selected = next((p for p in candidates if p.exists() and json.loads(p.read_text(encoding="utf-8")).get("runnable")), None)
    if selected is None:
        raise ValueError("没有可重新执行的已保存计划")
    plan = ExperimentPlan.model_validate_json(selected.read_text(encoding="utf-8"))
    plan.completion_continuation = completion_continuation
    episode_names = []
    if match_episodes:
        path = match_episodes.resolve()
        if not path.is_relative_to((root / "output").resolve()):
            raise ValueError("对照任务只能来自当前项目的已保存实验")
        episode_names = json.loads(path.read_text(encoding="utf-8"))["game_names"]
        if len(episode_names) != samples or len(set(episode_names)) != samples or not all(
                re.fullmatch(r"[A-Za-z0-9_-]+/[A-Za-z0-9_-]+", name) for name in episode_names):
            raise ValueError("对照任务标识不符合当前样本数或包含非法路径")
        game_paths = ["/datasets/alfworld/json_2.1.1/valid_unseen/" + name + "/game.tw-pddl" for name in episode_names]
        selection = ("matched_games = " + repr(game_paths) + "\n"
                     "assert all(game in env.game_files for game in matched_games), 'Baseline game unavailable'\n"
                     "env.game_files = matched_games\nenv.num_games = len(matched_games)\n"
                     "env = env.init_env(batch_size=1)")
        plan.patches.append(CompatibilityPatch(path="alfworld.ipynb", old="env = env.init_env(batch_size=1)",
                            new=selection, reason="固定为基线实际执行的同一组官方任务，用于接口兼容性对照，不使用专家动作或答案"))
    plan.reason = f"复用已核验的原仓库入口与计划；本次目标 {samples} 个样本，实际数据版本和规模见 datasets 与实测指标。"
    plan.data_source = json.dumps(dataset_context(resources), ensure_ascii=False)
    plan.differences_from_paper = [s for s in plan.differences_from_paper if not any(t in s for t in ("前6", "6条", "6个"))]
    plan.differences_from_paper.append(f"本次目标为 {samples} 个真实样本；仍为子集实验，不是论文完整指标复现。")
    if completion_continuation:
        plan.differences_from_paper.append("显式增加 legacy Completion 到聊天模型的续写格式 system 约束；这是提示接口兼容性对照，不是与原论文相同的模型输入。")
    for pin in pins or []:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.-]+", pin):
            raise ValueError("依赖修正必须是 package==version")
        package = pin.split("==")[0]
        replaced = False
        for command in plan.install:
            for index, value in enumerate(command.argv):
                if value == package or value.startswith(package + "=="):
                    command.argv[index] = pin
                    replaced = True
        if not replaced:
            raise ValueError("只能修正原计划已安装的依赖：" + package)
        plan.differences_from_paper.append("依据实际安装错误校正依赖版本：" + pin)
    validate_plan(plan, inventory)
    output = root / "output" / ("reproduction-resumed-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    folder = output / old["paper_id"]
    folder.mkdir(parents=True)
    save_json(folder / "source.json", source)
    save_json(folder / "inspection.json", inventory)
    save_json(folder / "plan.json", plan.model_dump())
    save_json(folder / "plan_origin.json", {"path": str(selected), "sha256": digest(selected),
                                           "dependency_pins": pins or [],
                                           "completion_continuation": completion_continuation,
                                           "matched_episodes": episode_names,
                                           "reason": "Source and dataset integrity checked; infrastructure fixed; no new initial planning call"})
    save_json(output / "datasets.json", [{k: v for k, v in r.items() if k != "host_path"} for r in resources])
    llm, github = LLM(settings, output), GitHub(root)
    planner = CodexPlanner(output / "codex")
    started = time.monotonic()
    try:
        graph = build_repository_workflow(llm, github, folder, samples,
                    progress=lambda stage, paper: progress(paper + " : " + stage), planner=planner,
                    datasets=resources, start_at="execute")
        paper = {"paper_id": old["paper_id"], "filename": old["filename"]}
        state = graph.invoke({"paper": paper, "candidate": {"url": source["url"]}, "source": source,
                              "repo_path": str(repo), "inventory": inventory, "plan": plan.model_dump(),
                              "events": [{"stage": "plan_reused", "time": time.time(), "status": "verified"}],
                              "status": "running"})
        result = {**old, "status": state["status"], "stage": state["stage"], "reason": state.get("reason", ""),
                  "scope": state["plan"]["scope"], "plan": state["plan"], "source_path": str(repo),
                  "commands": state.get("commands", []), "artifacts": state.get("artifacts", []),
                  "artifact_root": state.get("artifact_root"), "repair_attempts": state.get("attempts", 0),
                  "metrics": state.get("metrics", []), "assessment": state.get("assessment"),
                  "events": state["events"], "planning_backend": "codex", "codex_calls": planner.calls,
                  "plan_reused_from": str(selected), "datasets": dataset_context(resources), "full_paper_reproduced": False,
                  "completion_continuation": completion_continuation, "matched_episodes": episode_names,
                  "experiment_models": list({(r["provider"], r["model"]): {"provider": r["provider"], "model": r["model"]}
                      for r in llm.records if r.get("agent") == "原仓库实验/API" and r.get("status") == "ok"}.values())}
        save_json(folder / "result.json", result)
        summary = summarize([result], started)
        save_json(output / "reproduction.json", summary)
        return summary, output
    finally:
        llm.close()
        github.close()


if __name__ == "__main__":
    from dataclasses import replace
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("--samples", type=int, choices=range(1, 101), default=6)
    parser.add_argument("--max-calls", type=int, default=400)
    parser.add_argument("--pin", action="append", default=[])
    parser.add_argument("--initial-plan", action="store_true")
    parser.add_argument("--completion-continuation", action="store_true")
    parser.add_argument("--match-episodes", type=Path)
    arguments = parser.parse_args()
    result, path = resume(arguments.previous, replace(Settings.load(), max_calls=arguments.max_calls), arguments.samples,
                          progress=lambda message: print(message, flush=True), pins=arguments.pin, initial_plan=arguments.initial_plan,
                          completion_continuation=arguments.completion_continuation, match_episodes=arguments.match_episodes)
    print(json.dumps({"status": result["status"], "output": str(path), "completed": result["completed"]}, ensure_ascii=False))
