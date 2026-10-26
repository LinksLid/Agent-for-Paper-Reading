from contextlib import closing
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import time
import uuid
import re

from langgraph.checkpoint.sqlite import SqliteSaver

from .config import Settings
from .llm import LLM
from .reproduction import dump
from .workflow import build_workflow
from .repository_experiments import run_repository_experiments, reproduction_markdown


def run(settings: Settings, paths: list[Path], question: str, mode="verified", max_repairs=1,
        reproduce=False, samples=3, progress=None, run_id="", reproduction_backend="api") -> tuple[dict, Path]:
    if not paths or not question.strip():
        raise ValueError("请选择论文并填写阅读问题。")
    if mode not in {"single", "multi", "verified"} or not 0 <= max_repairs <= 2:
        raise ValueError("mode 或 max_repairs 不合法。")
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    if not all(c.isalnum() or c in "-_" for c in run_id):
        raise ValueError("run_id 只能包含字母、数字、横线和下划线。")
    run_dir = settings.root / "output" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    llm = LLM(settings, run_dir)
    started = time.monotonic()
    try:
        llm.choose_model()
        with closing(sqlite3.connect(run_dir / "checkpoints.sqlite", check_same_thread=False)) as connection:
            saver = SqliteSaver(connection)
            graph = build_workflow(llm, settings.root, run_dir, saver, mode, max_repairs, reproduce, samples,
                                   reproduction_backend=reproduction_backend)
            (run_dir / "workflow.mmd").write_text(graph.get_graph().draw_mermaid(), encoding="utf-8")
            config = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
            initial = {"question": question.strip(), "paths": [str(p.resolve()) for p in paths], "events": []}
            for update in graph.stream(initial, config, stream_mode="updates"):
                for node, values in update.items():
                    if progress:
                        progress(node, values)
            state = dict(graph.get_state(config).values)
        dump(run_dir / "state.json", state)
        manifest = {"status": "completed", "mode": mode, "model": llm.model,
                    "models_used": llm.models_used, "provider_switches": llm.switches,
                    "question": question, "papers": [{k: p[k] for k in ("filename", "paper_id", "sha256", "pages")}
                                                      for p in state["papers"]],
                    "max_repairs": max_repairs, "reproduce": reproduce,
                    "reproduction_backend": reproduction_backend,
                    "codex_calls": state["reproduction"].get("codex_calls", 0),
                    "api_calls": llm.calls, "seconds": round(time.monotonic() - started, 3),
                    "accepted_claims": len(state["accepted"]), "rejected_claims": len(state["rejected"]),
                    "review_rounds": state["review_round"], "reproduction_status": state["reproduction"]["status"],
                    "total_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in llm.records)}
        dump(run_dir / "manifest.json", manifest)
        return state, run_dir
    except Exception as error:
        dump(run_dir / "manifest.json", {"status": "failed", "error_type": type(error).__name__,
                                          "api_calls": llm.calls, "seconds": round(time.monotonic() - started, 3)})
        raise
    finally:
        llm.close()


def reproduce_saved(settings: Settings, run_dir: Path, samples=3, progress=None, backend="api"):
    run_dir = run_dir.resolve()
    if not run_dir.is_relative_to((settings.root / "output").resolve()):
        raise ValueError("只能对当前项目的阅读结果执行复现")
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    folder = run_dir / ("repository-experiments-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    llm = LLM(settings, folder)
    try:
        result = run_repository_experiments(state["papers"], llm, settings.root, folder, samples, progress, backend=backend)
        state["reproduction"] = result
        section = "## 实验复现\n\n" + "\n".join(reproduction_markdown(result)) + "\n"
        state["report"] = re.sub(r"## 实验复现\n.*?(?=\n## |\Z)", lambda _: section, state["report"], flags=re.S)
        manifest["reproduce"] = True
        manifest["reproduction_status"] = result["status"]
        manifest["reproduction_backend"] = backend
        manifest["codex_calls"] = manifest.get("codex_calls", 0) + result.get("codex_calls", 0)
        manifest["reproduction_output"] = str(folder)
        manifest["reproduction_seconds"] = result["seconds"]
        manifest["api_calls"] += llm.calls
        manifest["total_tokens"] += sum(r.get("usage", {}).get("total_tokens", 0) for r in llm.records)
        manifest["models_used"] = list({(m["provider"], m["model"]): m for m in manifest.get("models_used", []) + llm.models_used}.values())
        state["report"] = re.sub(r"API 调用：\d+", f"API 调用：{manifest['api_calls']}", state["report"])
        dump(run_dir / "state.json", state)
        dump(run_dir / "manifest.json", manifest)
        (run_dir / "report.md").write_text(state["report"], encoding="utf-8")
        return result
    finally:
        llm.close()
