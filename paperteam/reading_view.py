"""Reader-facing views; only verified paper facts enter the main explanation."""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess

import streamlit as st

from .understanding import PHASE_LABELS, method_dot, visible_improvements


def source_records(guide):
    records = []

    def add(label, item):
        if item and item.get("verified"):
            for evidence in item["evidence"]:
                records.append({"label": label, **evidence})

    for key, label in [("task", "研究任务"), ("inputs", "输入"), ("outputs", "输出"),
                       ("prior_approach", "原有做法"), ("bottleneck", "瓶颈")]:
        add(label, guide[key])
    for step in guide["steps"]:
        add("方法 · " + step["name"], step)
    for edge in guide["links"]:
        add("连接 · " + edge["source"] + " → " + edge["target"], edge)
    for change in visible_improvements(guide):
        for key, label in [("before", "原做法"), ("after", "新做法"), ("why", "原因"), ("reported_effect", "效果")]:
            add("改进 · " + change["name"] + " · " + label, change.get(key))
    for i, fact in enumerate(guide["reported_results"]):
        add(f"作者实验 {i+1}", fact)
    for i, fact in enumerate(guide["limitations"]):
        add(f"结论边界 {i+1}", fact)
    for term in guide["glossary"]:
        add("术语 · " + term["term"], term["meaning"])
    return records


def source_page(paper, page: int, cache: Path):
    if not isinstance(page, int) or not 1 <= page <= paper["pages"]:
        raise ValueError("PDF页码越界")
    digest = paper["sha256"]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("论文校验标识无效")
    path = Path(paper["path"])
    if path.suffix.lower() != ".pdf" or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("PDF内容与当前讲解不一致，请重新阅读")
    renderer = shutil.which("pdftoppm")
    if not renderer:
        return None
    prefix = cache / digest / ("page-" + str(page))
    image = prefix.with_suffix(".png")
    if not image.is_file():
        prefix.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([renderer, "-f", str(page), "-l", str(page), "-singlefile", "-scale-to", "1500",
                                 "-png", str(path), str(prefix)], capture_output=True, timeout=35)
        if result.returncode or not image.exists():
            raise RuntimeError("原文页图渲染失败，可下载原PDF查看该物理页")
    return image


def fact_text(item, missing="本轮尚未核实，需补查原文。"):
    return item["text"] if item and item.get("verified") else missing


def paper_guide(guide, paper, cache: Path, reproduction=None):
    st.subheader(paper["filename"].removesuffix(".pdf"))
    st.caption("先读任务，再顺着方法看改进；下方可逐项展开原文依据。")
    coverage = guide["coverage"]
    if coverage["missing"]:
        st.info("本轮仍需补查：" + "、".join(coverage["missing"]))

    st.markdown("#### 这篇论文究竟在做什么")
    with st.container(border=True):
        st.write(fact_text(guide["task"]))
        left, right = st.columns(2)
        left.markdown("**拿到什么（输入）**")
        left.write(fact_text(guide["inputs"]))
        right.markdown("**交付什么（输出）**")
        right.write(fact_text(guide["outputs"]))
    st.markdown("**原来怎么做：** " + fact_text(guide["prior_approach"]))
    st.markdown("**为什么还要改：** " + fact_text(guide["bottleneck"]))

    example = guide["teaching_example"]
    if example.get("verified"):
        with st.container(border=True):
            st.markdown("#### 用一个例子走一遍")
            st.caption("教学推演 · 不是论文原例 · 未实际运行")
            st.markdown("**假想输入**")
            st.write(example["input"])
            for i, step in enumerate(example["walkthrough"], 1):
                st.write(f"{i}. {step}")
            st.markdown("**输出是什么样**")
            st.write(example["output"])

    st.markdown("#### 方法怎样把输入变成输出")
    diagram = method_dot(guide)
    if diagram:
        st.graphviz_chart(diagram, width="stretch")
        st.caption("依据核验通过的原文整理的流程；训练、使用和评测阶段分开标注。")
    else:
        st.caption("步骤之间的连接证据还不充分，暂时只展示已核实模块。")
    for step in guide["steps"]:
        if not step.get("verified"):
            continue
        with st.container(border=True):
            st.markdown(f"**{step['step_id']} · {step['name']}** · {PHASE_LABELS[step['phase']]}")
            st.write("接收：" + step["receives"])
            st.write("操作：" + step["does"])
            st.write("交付：" + step["produces"])

    st.markdown("#### 相对已有做法，具体改了什么")
    changes = visible_improvements(guide)
    if changes:
        st.table([{"改进点": c["name"], "原有做法": fact_text(c["before"]), "本文做法": fact_text(c["after"]),
                   "为何这样改": fact_text(c.get("why"), "本轮未核实原因"),
                   "作者的对应效果证据": fact_text(c.get("reported_effect"), "未核实到该改动的独立效果证据")}
                  for c in changes])
    else:
        st.write("本轮未核实出充分的旧做法与新做法对照，不能只凭新模块名称断言创新。")

    st.markdown("#### 作者怎样验证，哪些结论仍有限制")
    for result in guide["reported_results"]:
        if result.get("verified"):
            st.write("论文报告：" + result["text"])
    for limitation in guide["limitations"]:
        if limitation.get("verified"):
            st.write("论文中的边界：" + limitation["text"])
    if reproduction:
        st.caption("本机运行与上述论文成绩的模型、样本和条件可能不同；详情在“论文实验复现”页。")
        metrics = reproduction.get("metrics", [])
        if metrics:
            st.write("本机子集实测：" + "；".join(str(m["name"]) + " = " + format(m["value"], ".4g") for m in metrics))
        elif reproduction.get("reason"):
            st.write("本机实验情况：" + reproduction["reason"])

    terms = [g for g in guide["glossary"] if g["meaning"].get("verified")]
    if terms:
        with st.expander("不熟悉这些术语？"):
            for term in terms:
                st.markdown("**" + term["term"] + "**：" + term["meaning"]["text"])

    with st.expander("核对上述讲解的原文与页码"):
        chunks = {c["chunk_id"]: c for c in paper["chunks"]}
        evidence = [r for r in source_records(guide) if r["chunk_id"] in chunks]
        if evidence:
            selection = st.selectbox("选择要核对的内容", range(len(evidence)),
                         format_func=lambda i: evidence[i]["label"] + " · PDF p." + str(chunks[evidence[i]["chunk_id"]]["page"]),
                         key="guide-source-" + paper["paper_id"])
            record = evidence[selection]
            chunk = chunks[record["chunk_id"]]
            st.caption(f"PDF物理页 {chunk['page']} · {record['chunk_id']}")
            st.text(record["quote"])
            if st.checkbox("显示这一页原图（可核对图表）", key="guide-page-" + paper["paper_id"]):
                try:
                    preview = source_page(paper, chunk["page"], cache)
                    if preview:
                        st.image(str(preview), caption=f"原文 PDF 第 {chunk['page']} 页", width="stretch")
                    else:
                        st.caption("未检测到页图渲染工具，请下载原PDF查看。")
                except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as error:
                    st.info(str(error))
        source = Path(paper["path"])
        if source.is_file():
            st.download_button("下载这篇论文原PDF", source.read_bytes(), file_name=source.name,
                               key="guide-pdf-" + paper["paper_id"])
