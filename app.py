from dataclasses import replace
import json
from pathlib import Path

import streamlit as st

from paperteam.config import ROOT, Settings
from paperteam.runner import run

st.set_page_config(page_title="PaperTeam · 多智能体论文阅读", page_icon="📚", layout="wide")
st.title("PaperTeam")
st.caption("LangGraph 多智能体论文精读 · 原文证据核验 · MAC-SQL 方法级实验验证")

with st.sidebar:
    st.subheader("本次阅读")
    files = sorted((ROOT / "paper").glob("*.pdf"))
    default = [p for p in files if any(key in p.name for key in ("CHESS", "MAC-SQL"))]
    selected = st.multiselect("论文（放入项目 paper 文件夹）", files, default=default or files[:2], format_func=lambda p: p.name)
    mode_label = st.selectbox("运行配置", ["分工 + 核验 + 返工", "仅多智能体分工", "单智能体基线"])
    mode = {"分工 + 核验 + 返工": "verified", "仅多智能体分工": "multi", "单智能体基线": "single"}[mode_label]
    reproduce = st.checkbox("运行 MAC-SQL 实验", value=False,
                              help="复用固定版本的官方提示词和评估函数，在自建数据库上验证；其余论文暂无运行适配器。")
    samples = st.slider("实验样本数", 1, 6, 3, disabled=not reproduce)
    model = st.text_input("模型名称（留空自动选择）", value="")
    provider_label = st.selectbox("API 策略", ["DeepSeek 优先，预算不足转同济", "仅同济"])
    max_calls = st.number_input("本次 API 调用上限", min_value=5, max_value=100, value=30)
    max_repairs = st.slider("最多补查轮次", 0, 2, 1, disabled=mode != "verified")
    st.caption("优先读取 deepseek.env，累计最多 20 元；备用接口读取 .env。缺少 deepseek.env 时直接使用 .env。")

question = st.text_area("你想从这些论文中了解什么？", value="比较这些论文的智能体分工、协作机制与实验设计，指出结论的证据和局限。", height=95)
start = st.button("开始协作阅读", type="primary", disabled=not selected or not question.strip())
if start:
    try:
        settings = replace(Settings.load(model=model.strip(), provider="tongji" if provider_label == "仅同济" else "auto"), max_calls=int(max_calls))
        with st.status("正在阅读论文", expanded=True) as status:
            labels = {"ingest": "建立论文证据索引", "planner": "制定阅读计划", "method_analyst": "分析方法",
                      "experiment_analyst": "分析实验", "verifier": "核验结论与原文", "targeted_repair": "根据反馈定向补查",
                      "reproducer": "处理实验复现", "reporter": "生成有引用的阅读报告", "single_reader": "完成单智能体阅读"}
            def progress(node, values):
                st.write("✓ " + labels.get(node, node))
            state, output = run(settings, selected, question, mode, max_repairs, reproduce, samples, progress)
            st.session_state["result_dir"] = str(output)
            status.update(label="阅读完成", state="complete", expanded=False)
    except Exception as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        st.error("运行停止：" + message)

saved = sorted((ROOT / "output").glob("*/manifest.json"), reverse=True) if (ROOT / "output").exists() else []
complete = [p.parent for p in saved if json.loads(p.read_text(encoding="utf-8")).get("status") == "completed"
            and (p.parent / "report.md").exists()]
if complete:
    default_dir = Path(st.session_state.get("result_dir", str(complete[0])))
    index = complete.index(default_dir) if default_dir in complete else 0
    output = st.selectbox("查看已完成的阅读", complete, index=index, format_func=lambda p: p.name)
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    a, b, c, d = st.columns(4)
    a.metric("通过核验的结论", manifest["accepted_claims"])
    b.metric("API 调用", manifest["api_calls"])
    c.metric("运行时间", f"{manifest['seconds']:.1f}s")
    d.metric("实验状态", manifest["reproduction_status"])
    report_tab, evidence_tab, audit_tab = st.tabs(["阅读报告", "原文证据", "协作与核验记录"])
    with report_tab:
        report = (output / "report.md").read_text(encoding="utf-8")
        st.download_button("下载 Markdown 报告", report, file_name="paperteam-report.md", mime="text/markdown")
        st.markdown(report)
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
        st.json({"plan": state.get("plan"), "events": state["events"], "review_history": state.get("review_history"),
                 "reproduction": state.get("reproduction"), "manifest": manifest})
else:
    st.info("选择论文并开始阅读。运行完成后，这里会显示带原文证据的报告和协作记录。")
