from dataclasses import replace
import json
import re
from pathlib import Path

import streamlit as st

from paperteam.config import ROOT, Settings
from paperteam.runner import run, reproduce_saved, explain_saved
from paperteam.reading_view import paper_guide
from paperteam.repository_experiments import STATUS_LABELS

st.set_page_config(page_title="PaperTeam · 多智能体论文阅读", page_icon="📚", layout="wide")
st.title("PaperTeam")
st.caption("先讲清任务，再看懂方法与改进 · 原文证据核验 · 原仓库实验")

with st.sidebar:
    st.subheader("本次阅读")
    files = sorted((ROOT / "paper").glob("*.pdf"))
    default = [p for p in files if any(key in p.name for key in ("CHESS", "MAC-SQL"))]
    selected = st.multiselect("论文（放入项目 paper 文件夹）", files, default=default or files[:2], format_func=lambda p: p.name)
    mode_label = st.selectbox("运行配置", ["分工 + 核验 + 返工", "仅多智能体分工", "单智能体基线"])
    mode = {"分工 + 核验 + 返工": "verified", "仅多智能体分工": "multi", "单智能体基线": "single"}[mode_label]
    reproduce = st.checkbox("查找 GitHub 并尝试论文实验", value=True,
                              help="逐篇搜索、核对并下载作者仓库，依据 README 运行实验；缺少数据、模型或环境时说明具体原因。")
    backend_label = st.selectbox("复现实验规划与修复", ["现有 API", "Codex CLI"],
                               help="Codex 使用本机 CLI 的登录账户，只负责计划和兼容补丁；实验仍在 Docker 中执行。")
    reproduction_backend = "codex" if backend_label == "Codex CLI" else "api"
    if reproduction_backend == "codex":
        st.caption("需安装 Codex CLI 并运行 codex login。Codex 单独消耗账户额度，不计入 DeepSeek 20 元预算；每次流程最多调用 10 次。")
    samples = st.slider("实验目标样本数", 1, 100, 3, disabled=not reproduce,
                        help="尽量选择原数据中的小规模子集；仅在原脚本可控制样本数时生效。")
    model = st.text_input("模型名称（留空自动选择）", value="")
    provider_label = st.selectbox("API 策略", ["DeepSeek 优先，预算不足转同济", "仅同济"])
    max_calls = st.number_input("本次 API 调用上限", min_value=5, max_value=150, value=80)
    max_repairs = st.slider("最多补查轮次", 0, 2, 1, disabled=mode != "verified")
    st.caption("优先读取 deepseek.env，累计最多 20 元；备用接口读取 .env。缺少 deepseek.env 时直接使用 .env。")

saved = sorted((ROOT / "output").glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True) if (ROOT / "output").exists() else []
complete = [p.parent for p in saved if json.loads(p.read_text(encoding="utf-8")).get("status") == "completed"
            and (p.parent / "report.md").exists()]
with st.expander("配置并开始新的阅读", expanded=not bool(complete)):
    question = st.text_area("你想从这些论文中了解什么？", value="逐篇讲清研究任务的输入输出，用一个例子解释方法流程；对照原有做法说明具体改进，并指出作者的实验证据与局限。", height=95)
    start = st.button("开始协作阅读", type="primary", disabled=not selected or not question.strip())
if start:
    try:
        settings = replace(Settings.load(model=model.strip(), provider="tongji" if provider_label == "仅同济" else "auto"), max_calls=int(max_calls))
        with st.status("正在阅读论文", expanded=True) as status:
            labels = {"ingest": "建立论文证据索引", "planner": "制定阅读计划", "method_analyst": "分析方法",
                      "experiment_analyst": "分析实验", "verifier": "核验结论与原文", "targeted_repair": "根据反馈定向补查",
                      "paper_explainer": "整理任务、方法、改进讲解并核验",
                      "reproducer": "处理实验复现", "reporter": "生成有引用的阅读报告", "single_reader": "完成单智能体阅读"}
            def progress(node, values):
                st.write("✓ " + labels.get(node, node))
            state, output = run(settings, selected, question, mode, max_repairs, reproduce, samples, progress,
                                reproduction_backend=reproduction_backend)
            st.session_state["result_dir"] = str(output)
            status.update(label="阅读完成", state="complete", expanded=False)
        st.rerun()
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        st.error("运行停止：" + message)

if complete:
    default_dir = Path(st.session_state.get("result_dir", str(complete[0])))
    index = complete.index(default_dir) if default_dir in complete else 0
    output = st.selectbox("查看已完成的阅读", complete, index=index, format_func=lambda p: p.name)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    guide_tab, report_tab, reproduction_tab, evidence_tab, audit_tab = st.tabs(["先读懂论文", "完整阅读报告", "论文实验复现", "原文证据", "协作与核验记录"])
    with guide_tab:
        guides = {g["paper_id"]: g for g in state.get("paper_guides", [])}
        selected_paper = st.selectbox("先读哪篇论文", state["papers"], format_func=lambda p: p["filename"])
        if st.button("更新这篇论文的讲解" if guides else "生成全部论文的讲解", type="primary"):
            try:
                settings = replace(Settings.load(model=model.strip(), provider="tongji" if provider_label == "仅同济" else "auto"), max_calls=int(max_calls))
                labels = {"guide_draft": "分问题阅读并整理讲解", "guide_verify": "核验讲解与教学示例", "guide_repair": "按缺口定向补查"}
                with st.status("正在生成可讲明白的论文说明", expanded=True) as status:
                    explain_saved(settings, output, max_repairs=max_repairs,
                                  progress=lambda stage, paper: st.write(paper + "：" + labels.get(stage, stage)),
                                  paper_ids=[selected_paper['paper_id']] if guides else None)
                    status.update(label="论文讲解已更新", state="complete")
                st.rerun()
            except Exception as error:
                st.error("讲解更新停止：" + (str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__))
        st.caption("讲解更新只调用阅读接口；保留已有实验结果。教学示例与论文原文、真实实验分别标注。")
        if guides:
            guide = guides.get(selected_paper["paper_id"])
            if guide:
                experiment = next((p for p in state.get("reproduction", {}).get("papers", []) if p["paper_id"] == selected_paper["paper_id"]), None)
                paper_guide(guide, selected_paper, ROOT / ".cache/pdf-pages", experiment)
            else:
                st.info("这篇论文尚未生成讲解，可通过上方按钮补齐。")
        else:
            st.info("这份历史结果仍是结论列表。生成讲解后，这里将展示输入输出、教学例子、方法流程和改进对照。")
    with report_tab:
        report = (output / "report.md").read_text(encoding="utf-8")
        st.download_button("下载 Markdown 报告", report, file_name="paperteam-report.md", mime="text/markdown")
        st.markdown(re.sub(r"```mermaid\n.*?```", "_流程图请见“先读懂论文”页。_", report, flags=re.S))
    with reproduction_tab:
        st.write("从当前阅读结果中的论文查找 GitHub、下载源码并执行仓库实验，无需重复论文阅读。")
        if st.button("为这些论文执行 GitHub 搜索与实验", type="primary"):
            try:
                settings = replace(Settings.load(model=model.strip(), provider="tongji" if provider_label == "仅同济" else "auto"), max_calls=int(max_calls))
                labels = {"search": "搜索与核对仓库", "download": "下载源码", "inspect": "检查运行说明",
                          "plan": "制定原仓库实验计划", "execute": "配置环境并执行", "repair": "依据运行日志修复", "assess": "核对结果"}
                with st.status("正在处理论文实验", expanded=True) as status:
                    result = reproduce_saved(settings, output, samples,
                                lambda stage, paper: st.write(f"{paper}：{labels.get(stage, stage)}"), backend=reproduction_backend)
                    status.update(label="论文实验流程结束", state="complete")
                st.rerun()
            except Exception as error:
                st.error("实验流程停止：" + (str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__))
        reproduction = state.get("reproduction", {})
        if reproduction.get("kind") == "repository_experiments":
            if reproduction.get("comparisons"):
                st.write("ReAct 文本接口兼容性对照（相同任务，提示输入有差异）")
                st.table([{"任务": row["task"], "原调用方式": f"{row['baseline_successes']}/{row['episodes']}",
                           "续写兼容": f"{row['continuation_successes']}/{row['episodes']}"}
                          for row in reproduction["comparisons"]])
            for item in reproduction["papers"]:
                with st.expander(item["filename"] + " · " + STATUS_LABELS.get(item["status"], item["status"]), expanded=True):
                    if item.get("repository"):
                        st.markdown(f"[GitHub 仓库]({item['repository']})")
                    if item.get("commit"):
                        st.caption("已下载源码版本：" + item["commit"])
                    if item.get("reason"):
                        st.write(item["reason"])
                    for missing in (item.get("plan") or {}).get("missing_requirements", []):
                        st.write("缺少条件：" + missing)
                    if item.get("metrics"):
                        st.json(item["metrics"])
                    if item.get("artifact_root"):
                        for relative in item.get("artifacts", []):
                            artifact = (Path(item["artifact_root"]) / relative).resolve()
                            if artifact.is_relative_to((ROOT / "output").resolve()) and artifact.is_file() and artifact.stat().st_size <= 10_000_000:
                                st.download_button("下载 " + Path(relative).name, artifact.read_bytes(), file_name=Path(relative).name,
                                                   key="artifact-" + item["paper_id"] + "-" + relative)
                    st.json(item, expanded=False)
        elif reproduction.get("status") == "disabled":
            st.info("该次历史阅读未启用复现。点击上方按钮即可单独执行。")
        else:
            st.info("这是旧版方法级验证记录。点击上方按钮可以运行逐篇 GitHub 仓库流程。")
    with evidence_tab:
        for paper in state["papers"]:
            with st.expander(paper["filename"]):
                st.write(f"PDF 共 {paper['pages']} 页；SHA256：{paper['sha256']}")
                citations = {e["chunk_id"] for claim in state["accepted"] if claim["paper_id"] == paper["paper_id"] for e in claim["evidence"]}
                for chunk in paper["chunks"]:
                    if chunk["chunk_id"] in citations:
                        st.caption(f"PDF p.{chunk['page']} · {chunk['chunk_id']}")
                        st.text(chunk["text"])
    with audit_tab:
        a, b, c, d = st.columns(4)
        a.metric("基础结论通过核验", manifest["accepted_claims"])
        b.metric("累计 API 调用", manifest["api_calls"])
        c.metric("原阅读运行时间", f"{manifest['seconds']:.1f}s")
        d.metric("实验状态", STATUS_LABELS.get(manifest["reproduction_status"], manifest["reproduction_status"]))
        st.json({"plan": state.get("plan"), "events": state["events"], "review_history": state.get("review_history"),
                 "paper_guides": state.get("paper_guides", []),
                 "reproduction": state.get("reproduction"), "manifest": manifest})
else:
    st.info("选择论文并开始阅读。运行完成后，这里会显示带原文证据的报告和协作记录。")
