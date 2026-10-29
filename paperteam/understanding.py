"""Evidence-backed explanations: task -> example -> method -> improvements."""
import html
import json
from pathlib import Path
import re
import time
import textwrap
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .corpus import retrieve
from .models import Evidence
from .reproduction import dump


class SourcedText(BaseModel):
    text: str = Field(min_length=2, max_length=550)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class MethodStep(BaseModel):
    step_id: str = Field(pattern=r"^S[1-9][0-9]?$" )
    name: str = Field(min_length=2, max_length=55)
    phase: Literal["training", "inference", "evaluation", "unspecified"] = "unspecified"
    receives: str = Field(min_length=2, max_length=220)
    does: str = Field(min_length=2, max_length=400)
    produces: str = Field(min_length=2, max_length=220)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class MethodLink(BaseModel):
    source: str
    target: str
    relation: str = Field(min_length=2, max_length=100)
    evidence: list[Evidence] = Field(min_length=1, max_length=3)


class Improvement(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    before: SourcedText
    after: SourcedText
    why: SourcedText | None = None
    reported_effect: SourcedText | None = None


class GlossaryTerm(BaseModel):
    term: str = Field(min_length=1, max_length=60)
    meaning: SourcedText


class TeachingExample(BaseModel):
    input: str = Field(min_length=4, max_length=600)
    walkthrough: list[str] = Field(min_length=2, max_length=7)
    output: str = Field(min_length=4, max_length=500)


class PaperExplanation(BaseModel):
    task: SourcedText
    inputs: SourcedText
    outputs: SourcedText
    bottleneck: SourcedText
    prior_approach: SourcedText
    steps: list[MethodStep] = Field(min_length=2, max_length=8)
    links: list[MethodLink] = Field(default_factory=list, max_length=12)
    improvements: list[Improvement] = Field(min_length=1, max_length=4)
    reported_results: list[SourcedText] = Field(default_factory=list, max_length=4)
    limitations: list[SourcedText] = Field(default_factory=list, max_length=3)
    glossary: list[GlossaryTerm] = Field(default_factory=list, max_length=6)
    teaching_example: TeachingExample

    @model_validator(mode="after")
    def coherent_ids(self):
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("方法步骤ID不能重复")
        if any(e.source not in ids or e.target not in ids for e in self.links):
            raise ValueError("流程连接必须引用真实步骤ID")
        return self


class ExplanationReview(BaseModel):
    readable: bool
    example_consistent: bool
    issues: list[str] = Field(default_factory=list, max_length=8)


PHASE_LABELS = {"training": "训练", "inference": "推理/使用", "evaluation": "评测", "unspecified": "方法步骤"}
SECTION_LABELS = {"task": "任务", "inputs_outputs": "输入与输出", "motivation": "原有做法与难点",
                  "method": "方法步骤", "connections": "步骤之间的关系", "improvements": "具体改进对照",
                  "results": "作者报告的验证", "example": "教学示例"}


def reading_context(paper, accepted=(), repair=False):
    """Retrieve independently for each question, with a bounded, auditable union."""
    anchors = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9_-]+", " ".join(c["text"] for c in accepted)))[:1800]
    questions = {
        "task": ["task problem definition input output motivation", "Introduction task setting problem formulation"],
        "method": ["framework overview architecture method algorithm pipeline", "training inference stages components " + anchors],
        "improvements": ["previous methods limitations contributions unlike existing approaches", "we propose improve novel contribution " + anchors],
        "experiments": ["main results baseline ablation benchmark dataset evaluation metric", "limitations discussion comparison experimental setup"],
    }
    selected, by_id = [], {}

    def add(chunk, section):
        if chunk["chunk_id"] in by_id:
            if section not in by_id[chunk["chunk_id"]]["reading_questions"]:
                by_id[chunk["chunk_id"]]["reading_questions"].append(section)
            return
        if sum(len(c["text"]) for c in selected) + len(chunk["text"]) > 52000:
            return
        item = {**chunk, "reading_questions": [section]}
        selected.append(item)
        by_id[chunk["chunk_id"]] = item

    for chunk in paper["chunks"]:
        if chunk["page"] <= 2:
            add(chunk, "opening")
    for section, queries in questions.items():
        hits = []
        for query in queries:
            hits.extend(retrieve(paper, [query], k=5 if repair else 4))
        page_counts = {}
        added = 0
        for chunk in hits:
            if chunk["chunk_id"] in by_id:
                add(chunk, section)
                continue
            if page_counts.get(chunk["page"], 0) >= 2:
                continue
            add(chunk, section)
            page_counts[chunk["page"]] = page_counts.get(chunk["page"], 0) + 1
            added += 1
            if added >= (8 if repair else 6):
                break
    return {"paper_id": paper["paper_id"], "filename": paper["filename"],
            "pages_included": sorted({c["page"] for c in selected}), "chunks": selected}


def explanation_claims(guide, paper_id):
    """Include every displayed label, stage, I/O and edge in evidence checking."""
    claims, locations = [], {}

    def add(key, item, kind="method", text=None):
        if item is None:
            return
        item.update(verified=False)
        locations[key] = item
        claims.append({"paper_id": paper_id, "kind": kind, "text": text or item["text"],
                       "evidence": item["evidence"], "guide_key": key})

    for key in ("task", "inputs", "outputs", "bottleneck", "prior_approach"):
        add(key, guide[key], "background" if key in {"bottleneck", "prior_approach"} else "method")
    names = {s["step_id"]: s["name"] for s in guide["steps"]}
    for step in guide["steps"]:
        phase = PHASE_LABELS[step["phase"]] + "阶段，" if step["phase"] != "unspecified" else ""
        text = (phase + step["name"] + "：输入为" + step["receives"] + "；执行" + step["does"] +
                "；输出为" + step["produces"] + "。")
        add("steps." + step["step_id"], step, text=text)
    for i, link in enumerate(guide["links"]):
        add(f"links.{i}", link, text=f"方法流程中，{names[link['source']]}到{names[link['target']]}的关系是：{link['relation']}")
    for i, change in enumerate(guide["improvements"]):
        for field in ("before", "after", "why", "reported_effect"):
            value = change.get(field)
            if value:
                prefix = {"before": "原有做法", "after": "本文改动", "why": "改动原因", "reported_effect": "作者报告的效果"}[field]
                add(f"improvements.{i}.{field}", value, "experiment" if field == "reported_effect" else "method",
                    text=f"{change['name']}，{prefix}：{value['text']}")
    for key, kind in (("reported_results", "experiment"), ("limitations", "limitation")):
        for i, fact in enumerate(guide[key]):
            add(f"{key}.{i}", fact, kind)
    for i, term in enumerate(guide["glossary"]):
        add(f"glossary.{i}", term["meaning"], text=term["term"] + "：" + term["meaning"]["text"])
    return claims, locations


def section_coverage(guide):
    verified = lambda x: bool(x and x.get("verified"))
    steps = [s for s in guide["steps"] if verified(s)]
    valid_ids = {s["step_id"] for s in steps}
    for edge in guide["links"]:
        if edge["source"] not in valid_ids or edge["target"] not in valid_ids:
            edge.update(verified=False, rejection_reason="连接的步骤尚未通过核验")
    sections = {
        "task": verified(guide["task"]),
        "inputs_outputs": verified(guide["inputs"]) and verified(guide["outputs"]),
        "motivation": verified(guide["prior_approach"]) and verified(guide["bottleneck"]),
        "method": len(steps) >= 2,
        "connections": any(verified(e) for e in guide["links"]),
        "improvements": any(verified(c["before"]) and verified(c["after"]) for c in guide["improvements"]),
        "results": any(verified(f) for f in guide["reported_results"]),
        "example": guide.get("teaching_example", {}).get("verified", False),
    }
    missing = [SECTION_LABELS[key] for key, ready in sections.items() if not ready]
    return {"sections": sections, "ready": sum(sections.values()), "total": len(sections), "missing": missing}


def verify_explanation(llm, draft, paper, checker):
    guide = draft.model_dump()
    claims, locations = explanation_claims(guide, paper["paper_id"])
    restorations = restore_extracted_quotes(claims, paper)
    accepted, rejected = checker(llm, claims, [paper], semantic=True)
    for claim in accepted:
        locations[claim["guide_key"]].update(verified=True)
    for issue in rejected:
        locations[issue["claim"]["guide_key"]].update(rejection_reason=issue["reason"])
    # The example is explicitly fictional. Check consistency with supported
    # mechanism, rather than pretending its entities or output occur in the PDF.
    review = llm.structured("综合报告员/讲解易懂性核验",
        "检查这份中文论文讲解是否让没有读过论文的人理解任务、输入输出、方法和旧新差异。"
        "教学示例是明确标注的虚构推演，不要求虚构实体在论文出现；但其步骤、输入输出类型必须与已核验方法一致。"
        "不能在示例中声称已运行、给出未测量的准确率或把它当论文原例。"
        "只评价已核验事实，未核验字段不能拿来补证。issues只给具体、关键的修改建议。\n" +
        "特别检查关键首步是否缺失、改进提到的关键模块是否在步骤中解释、输入输出是否具体；核心链条断裂时readable=false。\n" +
        json.dumps({"verified_facts": [{"key": c["guide_key"], "text": c["text"]} for c in accepted],
                    "example": guide["teaching_example"]}, ensure_ascii=False), ExplanationReview)
    guide["teaching_example"].update(origin="illustrative", verified=bool(review.example_consistent and
        guide["task"].get("verified") and guide["inputs"].get("verified") and guide["outputs"].get("verified") and
        sum(s.get("verified", False) for s in guide["steps"]) >= 2))
    guide.update(paper_id=paper["paper_id"], filename=paper["filename"], version=1,
                 audit={"accepted_facts": len(accepted), "rejected_facts": rejected, "readability": review.model_dump(),
                        "quote_restorations": restorations})
    guide["coverage"] = section_coverage(guide)
    guide["status"] = "ready" if not guide["coverage"]["missing"] and review.readable else "partial"
    return guide


def restore_extracted_quotes(claims, paper):
    """Restore literal PDF spelling only for case, whitespace and wrapped words.

    No approximate word matching, substitutions or cross-chunk stitching. The
    restored continuous source text still passes the usual semantic reviewer.
    """
    def indexed(text):
        wrapped = {i for m in re.finditer(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])", text) for i in range(*m.span())}
        result, positions = [], []
        for i, char in enumerate(text):
            if i in wrapped:
                continue
            if char.isspace():
                if result and result[-1] != ' ':
                    result.append(' ')
                    positions.append(i)
                continue
            for folded in char.casefold():
                result.append(folded)
                positions.append(i)
        return ''.join(result), positions

    chunks = {c['chunk_id']: c for c in paper['chunks']}
    changes = []
    for claim in claims:
        for evidence in claim['evidence']:
            chunk = chunks.get(evidence['chunk_id'])
            if not chunk or chunk['paper_id'] != claim['paper_id'] or evidence['quote'] in chunk['text']:
                continue
            canonical, positions = indexed(chunk['text'])
            query, _ = indexed(evidence['quote'])
            start = canonical.find(query) if len(query) >= 16 else -1
            if start < 0:
                continue
            restored = chunk['text'][positions[start]:positions[start + len(query) - 1] + 1]
            if len(restored) > 1000:
                continue
            changes.append({'key': claim['guide_key'], 'chunk_id': evidence['chunk_id'],
                            'generated_quote': evidence['quote'], 'source_quote': restored})
            evidence['quote'] = restored
    return changes


def generate_explanations(llm, papers, question, accepted=(), max_repairs=1, output=None, progress=None, checker=None, previous=None):
    if checker is None:
        from .workflow import check_claims
        checker = check_claims
    guides = []
    for paper in papers:
        started = time.monotonic()
        seed = [c for c in accepted if c["paper_id"] == paper["paper_id"]]
        old = (previous or {}).get(paper['paper_id'])
        history = []
        feedback = json.dumps({'previous_explanation': old, 'instruction': '参考上次缺口与核验意见重新组织，补齐核心模块和连续原文证据；不要重复同一错误。'}, ensure_ascii=False) if old else ""
        for attempt in range(min(max_repairs, 1) + 1):
            if progress:
                progress("guide_repair" if attempt else "guide_draft", paper["filename"])
            context = reading_context(paper, seed, repair=bool(attempt))
            prompt = ("请把当前论文讲给没有读过它的同学，目标不是堆砌结论，而是让人回答：任务是什么、方法怎样工作、相对已有做法改了什么。"
                "全部用中文，术语首次出现要用日常语言解释，保留原英文名辅助查找。"
                "task用一句话明确谁拿到什么输入、想得到什么输出；inputs/outputs分别说清具体数据类型，不能只说特征、知识或结果。"
                "prior_approach说明论文实际讨论的已有做法，bottleneck解释它卡在哪里，不能凭空否定所有旧研究。"
                "steps通常3至6步，逐步写输入、具体操作和输出，区别训练、使用和评测阶段；不是每篇论文都有多个智能体，禁止硬套多智能体。"
                "优先把核心使用流程讲完整，不能跳过关键首步；改进中提到的关键模块应出现在方法步骤里。不要为了讲训练或评测挤掉核心模块。"
                "links只画原文支持的真实依赖、反馈或数据流，不为好看补线；训练两阶段不能假装成每次推理的步骤。"
                "improvements通常2至3项，逐项对照before/after，why解释机制，reported_effect只写与该改动有直接关系的作者实验；"
                "没有对应实验证据时reported_effect=null，没有充分原文支持的原因则why=null。不要把系统总分当作每个组件的独立贡献。"
                "reported_results说明作者在什么数据、设置、指标和对照上验证；必须带实验范围，数值不确定时不写数值。"
                "所有论文事实包括图中节点、输入输出和连线都要有当前论文真实chunk_id和连续原文quote；引用应短而充分，不拼接。"
                "如果论文只主张某种优势，不要写成已经证明。无法核实的内容会被过滤并显示待补查，不要编造来填字段。"
                "teaching_example必须是教学推演，给一个小而具体的假想输入、沿方法的几步过程和输出形式；不冒充论文原例或已执行实验，"
                "不写虚构评测数字、不需要编造运行结果。例子要帮助理解该论文真实任务，不能把所有任务都讲成SQL。"
                "glossary列3至5个最影响理解的术语，以原文支持的通俗含义解释。每个事实简洁，通常一句话；一条quote优先控制在80至250字符。\n"
                "阅读问题：" + question + "\n已有核验结论（仅作为线索）：" + json.dumps(seed, ensure_ascii=False) +
                "\n本轮材料：" + json.dumps(context, ensure_ascii=False) + "\n需要修正的问题：" + feedback)
            draft = llm.structured("综合报告员/任务方法改进讲解", prompt, PaperExplanation, max_tokens=7500)
            if progress:
                progress("guide_verify", paper["filename"])
            guide = verify_explanation(llm, draft, paper, checker)
            history.append({"attempt": attempt, "context_pages": context["pages_included"],
                            "status": guide["status"], "coverage": guide["coverage"], "audit": guide["audit"]})
            if output:
                dump(Path(output) / paper["paper_id"] / f"guide-attempt-{attempt}.json", guide)
            if guide["status"] == "ready" or attempt >= max_repairs:
                break
            feedback = json.dumps({"missing_sections": guide["coverage"]["missing"],
                "rejected": [{"key": x["claim"]["guide_key"], "text": x["claim"]["text"], "reason": x["reason"]}
                             for x in guide["audit"]["rejected_facts"]],
                "readability": guide["audit"]["readability"], "previous_draft": draft.model_dump()}, ensure_ascii=False)
        guide["history"] = history
        guide["seconds"] = round(time.monotonic() - started, 3)
        guides.append(guide)
        if output:
            dump(Path(output) / paper["paper_id"] / "guide.json", guide)
    return guides


def visible_improvements(guide):
    return [c for c in guide.get("improvements", []) if c["before"].get("verified") and c["after"].get("verified")]


def method_dot(guide):
    steps = [s for s in guide.get("steps", []) if s.get("verified")]
    ids = {s["step_id"] for s in steps}
    edges = [e for e in guide.get("links", []) if e.get("verified") and e["source"] in ids and e["target"] in ids]
    if not edges:
        return ""
    lines = ['digraph Method {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.4; ranksep=0.55;',
             'node [shape=box, style="rounded,filled", fillcolor="#eef4ff", color="#6282b1", fontname="Microsoft YaHei", fontsize=14, margin="0.18,0.12"];',
             'edge [fontname="Microsoft YaHei", fontsize=12, color="#64748b"];']
    for step in steps:
        label = step['step_id'] + ' · ' + textwrap.fill(step['name'], 16)
        lines.append(step["step_id"] + ' [label=' + json.dumps(label, ensure_ascii=False) + '];')
    for phase in ("training", "inference", "evaluation"):
        group = [s["step_id"] for s in steps if s["phase"] == phase]
        if group:
            lines.append('subgraph cluster_' + phase + ' { label=' + json.dumps(PHASE_LABELS[phase], ensure_ascii=False) +
                         '; color="#cbd5e1"; ' + '; '.join(group) + '; }')
    for edge in edges:
        lines.append(edge["source"] + ' -> ' + edge["target"] + ' [label=' + json.dumps(textwrap.fill(edge["relation"], 18), ensure_ascii=False) + '];')
    return '\n'.join(lines + ['}'])


def method_mermaid(guide):
    steps = [s for s in guide.get("steps", []) if s.get("verified")]
    ids = {s["step_id"] for s in steps}
    edges = [e for e in guide.get("links", []) if e.get("verified") and e["source"] in ids and e["target"] in ids]
    if not edges:
        return ""
    label = lambda text: html.escape(text, quote=True).replace("\n", " ").replace("|", "/")
    lines = ['flowchart TB'] + [f'  {s["step_id"]}["{label(s["name"])}（{PHASE_LABELS[s["phase"]]}）"]' for s in steps]
    lines.extend(f'  {e["source"]} -->|"{label(e["relation"])}"| {e["target"]}' for e in edges)
    return '\n'.join(lines)


def explanation_markdown(guide, refs=lambda _: ""):
    def fact(item):
        return (item["text"].replace("\n", " ") + " " + refs(item["evidence"])) if item and item.get("verified") else "本轮尚未核实。"
    def cell(text):
        return text.replace("|", "／").replace("\n", " ")
    lines = ["### 1. 任务：这篇论文要解决什么", "", fact(guide["task"]), "",
             "| 输入 | 输出 |", "|---|---|", f"| {cell(fact(guide['inputs']))} | {cell(fact(guide['outputs']))} |", "",
             "**原有做法：** " + fact(guide["prior_approach"]), "", "**卡在哪里：** " + fact(guide["bottleneck"]), ""]
    example = guide.get("teaching_example", {})
    if example.get("verified"):
        lines += ["### 2. 用一个例子走一遍", "", "教学推演：不是论文原例，也未实际执行。", "",
                  "- 假想输入：" + example["input"], *[f"- 第{i+1}步：{s}" for i, s in enumerate(example["walkthrough"])],
                  "- 输出形式：" + example["output"], ""]
    lines += ["### 3. 方法：输入怎样变成输出", ""]
    flow = method_mermaid(guide)
    if flow:
        lines += ["根据已核验原文整理的流程；不等同于论文原图。", "", "```mermaid", flow, "```", ""]
        ids = {s["step_id"] for s in guide["steps"] if s.get("verified")}
        lines += ["流程连接依据：" + " ".join(refs(e["evidence"]) for e in guide["links"]
                                          if e.get("verified") and e["source"] in ids and e["target"] in ids), ""]
    for step in guide["steps"]:
        if step.get("verified"):
            lines += [f"**{step['step_id']} · {step['name']}（{PHASE_LABELS[step['phase']]}）** " + refs(step["evidence"]), "",
                      "- 接收：" + step["receives"], "- 操作：" + step["does"], "- 交付：" + step["produces"], ""]
    lines += ["### 4. 改进：相对已有做法具体改了什么", "", "| 改进点 | 原有做法 | 本文做法 | 为何这样改 | 作者提供的对应效果证据 |", "|---|---|---|---|---|"]
    for change in visible_improvements(guide):
        values = [change["name"], fact(change["before"]), fact(change["after"]), fact(change.get("why")), fact(change.get("reported_effect"))]
        lines.append("| " + " | ".join(cell(x) for x in values) + " |")
    lines += ["", "### 5. 作者如何验证，结论边界是什么", "", "以下为论文报告，独立的本机运行记录见实验复现部分。", ""]
    lines += ["- " + fact(f) for f in guide["reported_results"] if f.get("verified")]
    lines += ["- 局限：" + fact(f) for f in guide["limitations"] if f.get("verified")]
    terms = [t for t in guide["glossary"] if t["meaning"].get("verified")]
    if terms:
        lines += ["", "**术语小词典**", "", *["- " + t["term"] + "：" + fact(t["meaning"]) for t in terms]]
    if guide["coverage"]["missing"]:
        lines += ["", "本轮仍需补查：" + "、".join(guide["coverage"]["missing"]) + "。"]
    return '\n'.join(lines + [""])
