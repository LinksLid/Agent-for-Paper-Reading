import json
import operator
from pathlib import Path
import time
from typing import Annotated, TypedDict

from langgraph.graph import StateGraph, START, END

from .corpus import ingest, retrieve, evidence_errors
from .llm import LLM
from .models import Analysis, Plan, Review, ReportDraft
from .reproduction import dump
from .repository_experiments import run_repository_experiments, reproduction_markdown


class State(TypedDict, total=False):
    question: str
    paths: list[str]
    papers: list[dict]
    plan: dict
    methods: list[dict]
    experiments: list[dict]
    claims: list[dict]
    accepted: list[dict]
    rejected: list[dict]
    review_round: int
    review_history: list[dict]
    reproduction: dict
    report: str
    events: Annotated[list[dict], operator.add]


def event(name: str, **fields):
    return [{"node": name, "time": time.time(), **fields}]


def all_chunks(papers):
    return {c["chunk_id"]: c for p in papers for c in p["chunks"]}


def semantic_review(llm, claims: list[dict], chunks: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    if not claims:
        return [], []
    accepted, rejected = [], []
    # Small batches keep the verifier from silently dropping late claims.
    for start in range(0, len(claims), 16):
        batch = claims[start:start + 16]
        material = []
        for i, claim in enumerate(batch):
            material.append({"claim_index": i, "claim": claim,
                             "source_context": [chunks[e["chunk_id"]]["text"] for e in claim["evidence"]]})
        review = llm.structured("证据核验员", "逐条检查 claim 是否被所引原文充分支持。数字、比较范围、因果关系必须吻合；"
                                "区分作者报告与已复现结果。逐句判断，不能只因引用提到某个主题就接受整条结论。"
                                "标题或摘要不能证明其他论文没有某种机制，也不能证明整篇论文缺少某项内容。"
                                "不得使用外部知识补证。每个 claim_index 必须恰好有一个 verdict，"
                                "证据不足时 supported=false，并给出具体补查建议。\n" +
                                json.dumps(material, ensure_ascii=False), Review)
        indices = [v.claim_index for v in review.verdicts]
        if len(indices) != len(batch) or set(indices) != set(range(len(batch))):
            raise RuntimeError("核验结果遗漏或重复 claim_index，停止报告生成。")
        for verdict in sorted(review.verdicts, key=lambda x: x.claim_index):
            claim = batch[verdict.claim_index]
            if verdict.supported:
                accepted.append(claim)
            else:
                rejected.append({"claim": claim, "reason": verdict.reason})
    return accepted, rejected


def check_claims(llm, claims, papers, semantic=True):
    chunks = all_chunks(papers)
    allowed = {p["paper_id"] for p in papers}
    valid, rejected = [], []
    for claim in claims:
        errors = evidence_errors(claim, chunks, allowed)
        if errors:
            rejected.append({"claim": claim, "reason": "；".join(errors)})
        else:
            valid.append(claim)
    if semantic:
        accepted, issues = semantic_review(llm, valid, chunks)
        return accepted, rejected + issues
    return valid, rejected


def render_report(title, state, comparisons, questions, llm):
    papers = {p["paper_id"]: p for p in state["papers"]}
    chunks = all_chunks(state["papers"])
    sources = {}
    lines = [f"# {title}", "", f"阅读问题：{state['question']}", "",
             "模型：" + "、".join(f"`{p['provider']}/{p['model']}`" for p in getattr(llm, "models_used", []) or [{"provider": "test", "model": llm.model}]) + "。页码均为 PDF 物理页码，从 1 开始。", "",
             "文本抽取不保证完整还原双栏、表格和公式；核验为原文定位与模型语义检查，仍需人工抽查。", ""]

    def claim_line(claim):
        refs = []
        for evidence in claim["evidence"]:
            key = (evidence["chunk_id"], evidence["quote"])
            if key not in sources:
                sources[key] = f"S{len(sources) + 1:03d}"
            refs.append(f"[^{sources[key]}]")
        return "- " + claim["text"].replace("\n", " ") + " " + "".join(refs)

    for paper_id, paper in papers.items():
        lines.extend(["## " + paper["filename"], ""])
        claims = [c for c in state["accepted"] if c["paper_id"] == paper_id]
        for kind, label in [("background", "背景"), ("method", "方法"),
                            ("experiment", "实验"), ("limitation", "局限")]:
            selected = [c for c in claims if c["kind"] == kind]
            if selected:
                lines.extend(["### " + label, ""])
                lines.extend(claim_line(c) for c in selected)
                lines.append("")
        if not claims:
            lines.extend(["本轮未获得通过校验的结论。", ""])
    lines.extend(["## 跨论文分析", ""])
    lines.extend(claim_line(c) for c in comparisons)
    if not comparisons:
        lines.append("本轮未获得通过校验的跨论文比较；不同任务的指标不可直接排名。")
    lines.extend(["", "## 待人工研究的问题", ""])
    lines.extend("- " + q.replace("\n", " ") for q in questions)
    lines.extend(["", "## 实验复现", ""])
    reproduction = state.get("reproduction", {})
    if reproduction.get("kind") == "repository_experiments":
        lines.extend(reproduction_markdown(reproduction))
    elif reproduction.get("status") == "completed":
        lines.extend(["完成 MAC-SQL 方法级微型验证；未复现论文完整基准。", "",
                      f"- 样本：{reproduction['samples']}；数据库变体：{reproduction['database_variants']}。",
                      f"- 单次基线 EX：{reproduction['baseline_ex']:.1%}；分解与修正流程 EX：{reproduction['multi_ex']:.1%}。",
                      f"- 源码提交：`{reproduction['source']['commit']}`。", ""])
        lines.extend("- " + text for text in reproduction["limitations"])
    else:
        lines.append("状态：" + reproduction.get("status", "disabled") + "。" + reproduction.get("reason", ""))
    lines.extend(["", "## 核验与运行信息", "",
                  f"- 已保留结论：{len(state['accepted'])}；最终未通过结论：{len(state.get('rejected', []))}。",
                  f"- 核验轮次：{state.get('review_round', 0)}；API 调用：{llm.calls}。",
                  "- 历轮退回原因见 state.json 的 review_history；调用时间与 token 用量见 api_usage.jsonl。",
                  "", "## 原文证据", ""])
    for (chunk_id, quote), key in sources.items():
        chunk = chunks[chunk_id]
        paper = papers[chunk["paper_id"]]
        lines.extend([f"[^{key}]: {paper['filename']}，PDF p.{chunk['page']}，`{chunk_id}`。",
                      "    > " + quote.replace("\n", " "), ""])
    return "\n".join(lines)


def build_workflow(llm: LLM, root: Path, run_dir: Path, checkpointer=None,
                   mode="verified", max_repairs=1, reproduce=False, samples=3):
    def ingest_node(state):
        papers = ingest([Path(p) for p in state["paths"]], root / ".cache/papers")
        return {"papers": papers, "events": event("ingest", papers=len(papers))}

    def plan_node(state):
        inputs = [{"paper_id": p["paper_id"], "filename": p["filename"],
                   "abstract_context": p["chunks"][0]["text"]} for p in state["papers"]]
        plan = llm.structured("阅读规划员", "针对阅读问题提出英文检索关键词组；不同领域的论文应分别解释，"
                              "只比较有意义的机制，不强行比较指标。\n问题：" + state["question"] + "\n论文：" +
                              json.dumps(inputs, ensure_ascii=False), Plan)
        return {"plan": plan.model_dump(), "events": event("planner")}

    def analyze_paper(paper, role, queries, question, feedback=""):
        context = retrieve(paper, queries, k=7)
        kind = "method" if role == "方法分析员" else "experiment"
        prompt = (f"整体阅读问题：{question}\n当前论文 paper_id={paper['paper_id']}。你的角色是{role}。"
                  "只解释当前论文与整体问题相关的事实，跨论文比较由后续报告员负责。"
                  "禁止评论输入是否包含其他论文，禁止把检索缺口当成论文自身的局限。"
                  f"生成 2 至 3 条精确、可核验的结论；kind 主要使用 {kind}，必要时使用 limitation。"
                  "每条只能包含一个主要判断，并附一段足够支持该判断的原文。不得引用其他论文的结论。"
                  + ("只分析实验设置、结果、消融和实验局限，不要复述系统组成。" if kind == "experiment" else "聚焦方法、假设和协作机制。") +
                  "实验结论注明这是论文报告的数据，避免当成独立验证。\n补查反馈：" + feedback +
                  "\n检索材料：\n" + json.dumps(context, ensure_ascii=False))
        return [{**c.model_dump(), "origin_role": role} for c in llm.structured(role, prompt, Analysis).claims]

    def methods_node(state):
        claims = []
        for paper in state["papers"]:
            claims.extend(analyze_paper(paper, "方法分析员", state["plan"]["method_queries"], state["question"]))
        return {"methods": claims, "events": event("method_analyst", claims=len(claims))}

    def experiments_node(state):
        claims = []
        for paper in state["papers"]:
            claims.extend(analyze_paper(paper, "实验分析员", state["plan"]["experiment_queries"], state["question"]))
        return {"experiments": claims, "events": event("experiment_analyst", claims=len(claims))}

    def single_node(state):
        context = []
        for paper in state["papers"]:
            context.extend(retrieve(paper, [state["question"], "method experiments results limitations"], k=10))
        answer = llm.structured("单智能体基线", "独立完成所有论文的方法及实验阅读，每篇生成 4 至 6 条有原文引用的结论。"
                                "问题：" + state["question"] + "\n材料：" +
                                json.dumps(context, ensure_ascii=False), Analysis)
        return {"methods": [c.model_dump() for c in answer.claims], "experiments": [], "events": event("single_reader")}

    def verify_node(state):
        claims = state.get("claims", state.get("methods", []) + state.get("experiments", []))
        accepted, rejected = check_claims(llm, claims, state["papers"], semantic=mode == "verified")
        round_number = state.get("review_round", 0) + 1
        history = state.get("review_history", []) + [{"round": round_number, "accepted": len(accepted), "rejected": rejected}]
        return {"claims": claims, "accepted": accepted, "rejected": rejected,
                "review_round": round_number, "review_history": history,
                "events": event("verifier", accepted=len(accepted), rejected=len(rejected))}

    def repair_node(state):
        repaired = list(state["accepted"])
        for paper in state["papers"]:
            issues = [i for i in state["rejected"] if i["claim"]["paper_id"] == paper["paper_id"]]
            for role, kind, query_key in [("方法分析员", "method", "method_queries"),
                                          ("实验分析员", "experiment", "experiment_queries")]:
                relevant = [i for i in issues if i["claim"].get("origin_role", "实验分析员" if
                            i["claim"]["kind"] == "experiment" else "方法分析员") == role]
                if relevant:
                    # Replace that role's complete card, instead of appending a
                    # regenerated card to its previously accepted duplicate facts.
                    repaired = [c for c in repaired if not (c["paper_id"] == paper["paper_id"] and
                                c.get("origin_role") == role)]
                    feedback = json.dumps(relevant, ensure_ascii=False)
                    # The original claim text often contains English technical names useful for retrieval.
                    queries = state["plan"][query_key] + [i["claim"]["text"] for i in relevant]
                    repaired.extend(analyze_paper(paper, role, queries, state["question"], feedback))
        return {"claims": repaired, "events": event("targeted_repair")}

    def reproduce_node(state):
        if not reproduce:
            return {"reproduction": {"status": "disabled"}, "events": event("reproducer", status="disabled")}
        try:
            result = run_repository_experiments(state["papers"], llm, root, run_dir / "reproduction", samples=samples)
        except Exception as error:
            result = {"status": "failed", "reason": f"仓库实验流程未完成（{type(error).__name__}），详见 reproduction 目录。"}
        return {"reproduction": result, "events": event("reproducer", status=result["status"])}

    def report_node(state):
        draft = llm.structured("综合报告员", "基于已核验结论拟定中文标题、最多 3 条跨论文比较和待研究问题。"
                               "比较结论 kind=comparison，每条引用至少两篇论文的证据；单篇输入 comparisons=[]。"
                               "没有证据支持的差异不要写。待研究问题只提出问题，不加入新事实。\n问题：" + state["question"] +
                               "\n已核验结论：" + json.dumps(state["accepted"], ensure_ascii=False), ReportDraft)
        comparisons, dropped = check_claims(llm, [c.model_dump() for c in draft.comparisons],
                                            state["papers"], semantic=mode == "verified")
        chunks = all_chunks(state["papers"])
        comparisons = [c for c in comparisons if len({chunks[e["chunk_id"]]["paper_id"] for e in c["evidence"]}) >= 2]
        report = render_report(draft.title, state, comparisons, draft.open_questions, llm)
        (run_dir / "report.md").write_text(report, encoding="utf-8")
        dump(run_dir / "comparison_audit.json", {"accepted": comparisons, "rejected": dropped})
        return {"report": report, "events": event("reporter", comparisons=len(comparisons))}

    graph = StateGraph(State)
    nodes = {"ingest": ingest_node, "planner": plan_node, "method_analyst": methods_node,
             "experiment_analyst": experiments_node, "single_reader": single_node,
             "verifier": verify_node, "targeted_repair": repair_node,
             "reproducer": reproduce_node, "reporter": report_node}
    selected = ["ingest", "verifier", "reproducer", "reporter"]
    selected += ["single_reader"] if mode == "single" else ["planner", "method_analyst", "experiment_analyst"]
    if mode == "verified" and max_repairs:
        selected.append("targeted_repair")
    for name in selected:
        graph.add_node(name, nodes[name])
    graph.add_edge(START, "ingest")
    if mode == "single":
        graph.add_edge("ingest", "single_reader")
        graph.add_edge("single_reader", "verifier")
    else:
        graph.add_edge("ingest", "planner")
        graph.add_edge("planner", "method_analyst")
        graph.add_edge("planner", "experiment_analyst")
        graph.add_edge(["method_analyst", "experiment_analyst"], "verifier")
    if mode == "verified" and max_repairs:
        graph.add_conditional_edges("verifier", lambda s: "targeted_repair" if s["rejected"] and
                                    s["review_round"] <= max_repairs else "reproducer")
        graph.add_edge("targeted_repair", "verifier")
    else:
        graph.add_edge("verifier", "reproducer")
    graph.add_edge("reproducer", "reporter")
    graph.add_edge("reporter", END)
    return graph.compile(checkpointer=checkpointer)
