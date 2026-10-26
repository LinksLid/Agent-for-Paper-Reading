import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

from .config import ROOT, Settings
from .corpus import ingest
from .llm import LLM
from .reproduction import checkout
from .runner import run
from .repository_experiments import run_repository_experiments


def main():
    parser = argparse.ArgumentParser(description="PaperTeam / LangGraph 论文阅读与实验验证")
    sub = parser.add_subparsers(dest="command", required=True)
    model_list = sub.add_parser("models", help="列出 API 可用模型；不输出密钥")
    model_list.add_argument("--provider", choices=["auto", "deepseek", "tongji"], default="auto")
    index = sub.add_parser("index", help="本地索引论文，不调用 API")
    index.add_argument("--paper-dir", type=Path, default=ROOT / "paper")
    sub.add_parser("fetch-macsql", help="下载并校验固定版本的 MAC-SQL 源码")
    data = sub.add_parser("fetch-data", help="准备可核验的官方评测数据，不调用模型")
    data.add_argument("--dataset", choices=["bird", "alfworld"], required=True)
    data.add_argument("--samples", type=int, choices=range(1, 101), default=6)
    read = sub.add_parser("read", help="运行阅读工作流")
    read.add_argument("--paper-dir", type=Path, default=ROOT / "paper")
    read.add_argument("--paper", action="append", help="按文件名片段筛选，可重复")
    read.add_argument("--question", default="这些论文解决什么问题，方法如何协作，实验支持哪些结论？请区分任务差异并指出证据缺口。")
    read.add_argument("--mode", choices=["single", "multi", "verified"], default="verified")
    read.add_argument("--max-repairs", type=int, choices=[0, 1, 2], default=1)
    read.add_argument("--reproduce", action="store_true")
    read.add_argument("--reproduction-backend", choices=["api", "codex"], default="api",
                      help="原仓库实验规划与修复后端；codex 使用本机 CLI 登录账户，单独消耗额度")
    read.add_argument("--samples", type=int, choices=range(1, 101), metavar="1-100", default=3)
    read.add_argument("--model", default="")
    read.add_argument("--provider", choices=["auto", "deepseek", "tongji"], default="auto")
    read.add_argument("--max-calls", type=int)
    read.add_argument("--run-id", default="")
    repro = sub.add_parser("reproduce", help="逐篇搜索GitHub、下载并尝试原仓库实验")
    repro.add_argument("--paper-dir", type=Path, default=ROOT / "paper")
    repro.add_argument("--paper", action="append")
    repro.add_argument("--samples", type=int, choices=range(1, 101), metavar="1-100", default=3)
    repro.add_argument("--model", default="")
    repro.add_argument("--provider", choices=["auto", "deepseek", "tongji"], default="auto")
    repro.add_argument("--max-calls", type=int, default=80)
    repro.add_argument("--reproduction-backend", choices=["api", "codex"], default="api")
    args = parser.parse_args()
    if args.command == "index":
        papers = ingest(sorted(args.paper_dir.glob("*.pdf")), ROOT / ".cache/papers")
        print(json.dumps([{k: p[k] for k in ("filename", "paper_id", "pages", "github_urls")} for p in papers],
                         ensure_ascii=False, indent=2))
        return
    if args.command == "fetch-macsql":
        path, lock = checkout(ROOT)
        print(json.dumps({"path": str(path), "commit": lock["commit"]}))
        return
    if args.command == "fetch-data":
        from .datasets import download_bird, download_alfworld
        path, descriptor = download_bird(ROOT, args.samples) if args.dataset == "bird" else download_alfworld(ROOT)
        print(json.dumps({"path": str(path), "name": descriptor["name"], "samples": descriptor.get("samples"),
                          "games": descriptor.get("game_files")}, ensure_ascii=False))
        return
    settings = Settings.load(model=getattr(args, "model", ""), provider=args.provider)
    if getattr(args, "max_calls", None):
        settings = replace(settings, max_calls=args.max_calls)
    if args.command == "models":
        llm = LLM(settings)
        try:
            print(json.dumps({"available": llm.models(), "selected": llm.choose_model()}, ensure_ascii=False, indent=2))
        finally:
            llm.close()
    elif args.command == "reproduce":
        from datetime import datetime
        import uuid
        output = ROOT / "output" / ("reproduction-" + datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
        llm = LLM(settings, output)
        try:
            paths = sorted(args.paper_dir.glob("*.pdf"))
            if args.paper:
                paths = [p for p in paths if any(pattern.lower() in p.name.lower() for pattern in args.paper)]
            papers = ingest(paths, ROOT / ".cache/papers")
            result = run_repository_experiments(papers, llm, ROOT, output, args.samples,
                         progress=lambda stage, paper: print(f"{paper}：{stage}", flush=True), backend=args.reproduction_backend)
            print(json.dumps({"status": result["status"], "output": str(output),
                              "downloaded": result["downloaded"], "completed": result["completed"],
                              "codex_calls": result["codex_calls"]}, ensure_ascii=False))
        finally:
            llm.close()
    else:
        paths = sorted(args.paper_dir.glob("*.pdf"))
        if args.paper:
            paths = [p for p in paths if any(pattern.lower() in p.name.lower() for pattern in args.paper)]
        state, output = run(settings, paths, args.question, args.mode, args.max_repairs,
                            args.reproduce, args.samples, lambda n, _: print(f"完成节点：{n}", flush=True), args.run_id,
                            reproduction_backend=args.reproduction_backend)
        print(json.dumps({"output": str(output), "accepted": len(state["accepted"]),
                          "reproduction": state["reproduction"]["status"]}, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        main()
    except Exception as error:
        # Keep unexpected exceptions concise; HTTP exceptions could contain provider details.
        if isinstance(error, (ValueError, RuntimeError, FileExistsError)):
            print(f"运行停止：{error}", file=sys.stderr)
        else:
            print(f"运行停止（{type(error).__name__}），请检查依赖、输入与网络。", file=sys.stderr)
        sys.exit(1)
